"""The agents that get the call to a human.

Two phases before the interview, told apart by what the line is doing. A menu is a
recording that pauses between its options, so the agent waits well past a pause
before acting. Once the system has put the call in line for a representative,
nothing pauses any more: the next voice is a person finishing a sentence, so the
wait drops to the interview's and the model starts generating before the turn is
confirmed. Both phases end the same way -- a live representative speaks -- and the
job is over the moment one of them hands off to the InterviewAgent.

Which phase the call is in is something the model states, by calling
`hold_for_representative` when the system says it is transferring, rather than
something guessed from the words in the transcript. It is the model that has just
read the announcement; a queue's pickup line is whatever the person says.

The model decides what to do from the transcript alone: stay quiet, speak a menu
option, key digits, or hand off. Nothing here second-guesses it. Keying goes
through LiveKit's own `send_dtmf_events`, with successful results suppressed so
the agent gives the line time to respond instead of generating another action.
"""

import logging
from datetime import date

from livekit.agents import (
    Agent,
    ModelSettings,
    RunContext,
    TurnHandlingOptions,
    function_tool,
    inference,
    llm,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.beta.tools import send_dtmf_events as sdk_send_dtmf_events
from livekit.agents.beta.workflows.utils import DtmfEvent

from dispatch import CallSpec
from interview import InterviewAgent
from prompts import Phase, call_date_today, instructions

logger = logging.getLogger(__name__)

PROMPT = "navigator"

# The navigator's own model, overriding the session's. The two phases have
# opposite constraints: the interview answers a waiting human inside a tight
# budget on calibrated gpt-4.1, while a menu tolerates a couple of seconds, so
# the navigator gets the newer model. The tests run against this constant.
NAVIGATOR_LLM = "openai/gpt-5.6-luna"
# Takes over only when luna's endpoint errors, never on output quality. Chosen
# from a different vendor so one provider outage cannot take both. Historical
# results are in docs/decisions.md#model-selection.
NAVIGATOR_FALLBACK_LLM = "google/gemini-3.8-flash"

# Phase one. A menu pauses between its options, so a wait shorter than the pause
# answers a question that is still being asked. Preemptive generation is off for
# the same reason: continued menu speech invalidates speculative results.
# Speech uses a tool that bypasses pipeline TTS, so preemptive TTS adds no benefit.
MENU_TURN_HANDLING = TurnHandlingOptions(
    preemptive_generation={"enabled": False},
    endpointing={"min_delay": 1.5, "max_delay": 3.0},
)

# Phase two. No endpointing of its own: the session's, which is the interview's,
# so the whole human half of the call waits the same. Start the model before
# the person's turn is confirmed to reduce pickup latency; speech still uses
# a tool rather than preemptive pipeline TTS.
HOLD_TURN_HANDLING = TurnHandlingOptions(
    preemptive_generation={"enabled": True, "preemptive_tts": False},
)


def navigator_llm() -> llm.LLM:
    return llm.FallbackAdapter(
        [
            inference.LLM(model=model, extra_kwargs={"parallel_tool_calls": False})
            for model in (NAVIGATOR_LLM, NAVIGATOR_FALLBACK_LLM)
        ]
    )


@function_tool(description=sdk_send_dtmf_events.info.description)
async def send_dtmf_events(ctx: RunContext, events: list[DtmfEvent]) -> str | None:
    result = await sdk_send_dtmf_events(ctx, events)
    # The SDK returns text even on success, which otherwise starts another LLM
    # turn. Preserve failures (and unfamiliar results) so recovery stays possible.
    if result.startswith("Successfully sent DTMF events:"):
        return None
    return result


class _PhoneSystemAgent(Agent):
    """What both phases share: how they act on the line, and how they hand off.

    The differences between them are the arguments: the prompt's phase, and the
    turn handling that phase needs.
    """

    def __init__(
        self,
        *,
        spec: CallSpec,
        phase: Phase,
        turn_handling: TurnHandlingOptions,
        call_date: date,
        llm_model: llm.LLM | None = None,
        chat_ctx: llm.ChatContext | None = None,
    ) -> None:
        # llm_model exists for the tests, which run against one model at a time
        # rather than the fallback pair.
        #
        # The prompt carries the values as the dispatcher sends them, nothing
        # derived. How a menu takes a value is a fact about the payer, not the
        # value: one line wants the member ID without its letter, another wants
        # the letter keyed as a digit, a third wants it spoken. A human caller
        # decides that per line, and so does the model, helped by
        # `ivr_instructions` -- operator notes about a known payer that the
        # dispatcher sends and the prompt renders when present.
        # The SDK's own hang-up, the same one the interview uses. end_instructions
        # is None so no goodbye is generated: the session closes as soon as the
        # tool's turn is done, then the job deletes the room, dropping the line.
        end_call_tool = EndCallTool(
            extra_description=(
                "For this call, the line cannot reach a person: a voicemail greeting or "
                "a beep asking you to leave a message, a fax tone, or a recording that "
                "the office is closed with no option to hold."
            ),
            delete_room=True,
            end_instructions=None,
        )
        # Resolved once, so the phase switch can carry the same adapter over. A
        # second navigator_llm() would forget which model the menu had already
        # failed over from, and re-pay that failover on the turn a person picks
        # up; its clients would also outlive the agent that built them.
        llm_model = llm_model or navigator_llm()
        super().__init__(
            instructions=instructions(PROMPT, spec, phase=phase),
            llm=llm_model,
            turn_handling=turn_handling,
            chat_ctx=chat_ctx,
            # Keep the SDK's keying implementation and description; only a
            # successful result is silenced. Both phases keep it: a system that says it
            # is transferring and then asks for the member ID again is still a
            # menu, and the agent has to be able to answer it.
            tools=[send_dtmf_events, *end_call_tool.tools],
        )
        # The same call specification follows the conversation through the handoff.
        self._spec = spec
        # Dial-time date, handed to the interview so the whole call agrees on it.
        self._call_date = call_date
        self._llm_model = llm_model

    async def llm_node(self, chat_ctx, tools, model_settings):
        # The prompt asks for one tool call and no text; this makes it a rule the
        # model cannot break. Plain text from this agent would be synthesized
        # straight into the payer's menu, so there is no turn where text is right.
        # Per-agent on purpose: the interview agent must keep free text.
        #
        # Successful speech/keypad actions return None and end the tool chain.
        # Errors can still request recovery. If recovery exhausts max_tool_steps,
        # the SDK drops the forced call rather than speaking text into the menu.
        model_settings = ModelSettings(tool_choice="required")
        return Agent.default.llm_node(self, chat_ctx, tools, model_settings)

    @function_tool()
    async def wait(self) -> None:
        """Stay silent as the only action this turn.

        Use it while the system is still talking or listing options, is playing
        hold music, is announcing a transfer or a wait time, or is looking up an
        entry you just gave it. Never acknowledge a recording out loud.
        Do not combine this with speech or another tool. After another action,
        finish the turn; the session already listens for the line's response.
        """
        # Returning None is what keeps the line quiet: a tool with no output gets
        # no spoken follow-up. Asked to "say nothing", a chat model says "Okay."

    @function_tool()
    async def speak(self, text: str) -> None:
        """Say these exact words into the line, for a menu that asks you to SAY something.

        A menu that lists things to say and announces no keys is answered only
        with this tool.

        Args:
            text: Only the words the menu asked for, e.g. "benefits and eligibility".
                For a value, use THIS CALL; write identifiers digit by digit with
                spaces ("0 0 1 2") so they are read as digits, and a date as a date.
        """
        # Speech needs a tool too: every navigator action uses the same channel,
        # including answers to menus that accept speech instead of keypad input.
        # The tool call already carries the words in the chat history; letting
        # say() add them again as an assistant message records every utterance
        # twice, and makes it look like the model wrote text on its own.
        self.session.say(text, add_to_chat_ctx=False)

    @function_tool()
    async def representative_answered(self, opening: str) -> InterviewAgent:
        """Hand off only after a live human speaks, not when the IVR offers or starts a transfer.

        Args:
            opening: Your reply to the representative, spoken the moment you hand
                off, following WHEN A PERSON ANSWERS.
        """
        logger.info("Representative answered; handing off to the interview")
        # The opening rides on this call, so the representative hears it as soon as
        # the model has decided to hand off. Carry the conversation turns but not
        # this agent's instructions: the interview has its own, and a second system
        # prompt would compete with it.
        return InterviewAgent(
            spec=self._spec,
            chat_ctx=self.chat_ctx.copy(exclude_instructions=True),
            call_date=self._call_date,
            opening=opening,
        )


class HoldAgent(_PhoneSystemAgent):
    """Phase two: in line for a representative, with nothing left to answer."""

    def __init__(
        self,
        *,
        spec: CallSpec,
        call_date: date,
        llm_model: llm.LLM | None = None,
        chat_ctx: llm.ChatContext | None = None,
    ) -> None:
        super().__init__(
            spec=spec,
            phase="hold",
            turn_handling=HOLD_TURN_HANDLING,
            call_date=call_date,
            llm_model=llm_model,
            chat_ctx=chat_ctx,
        )


class NavigatorAgent(_PhoneSystemAgent):
    """Phase one: the automated menu."""

    def __init__(
        self,
        spec: CallSpec,
        *,
        llm_model: llm.LLM | None = None,
    ) -> None:
        super().__init__(
            spec=spec,
            phase="menu",
            turn_handling=MENU_TURN_HANDLING,
            call_date=call_date_today(),
            llm_model=llm_model,
        )

    # No on_enter. The navigator never speaks first: when the call connects the
    # payer's system is talking and nothing has asked us anything yet. Avoid an
    # extra generation that could overlap the first real turn.

    @function_tool()
    async def hold_for_representative(self) -> HoldAgent:
        """Start waiting for a person, once the system has finished with you.

        Call this when the system says it is transferring you, connecting you, or
        putting you in line for the next available representative, and it is no
        longer asking you for anything. Do not call it while a menu is still
        listing options or waiting for an entry.
        """
        logger.info(
            "In the queue; waiting for a representative on the interview's timing"
        )
        # A phase is an agent because the two settings that matter here, the
        # end-of-turn wait and preemptive generation, are read when an agent starts
        # its turn; only the wait can be changed on a running one.
        return HoldAgent(
            spec=self._spec,
            chat_ctx=self.chat_ctx.copy(exclude_instructions=True),
            call_date=self._call_date,
            llm_model=self._llm_model,
        )
