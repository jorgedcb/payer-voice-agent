"""The agent that talks to a live representative.

This is the third and final phase of a payer call. It is only ever reached by a
handoff from the navigator (see navigator.py), which carries over what the
representative already said, so this agent starts mid-conversation rather than
at silence.
"""

import logging
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any

from livekit.agents import Agent, RunContext, ToolError, function_tool, llm
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.job import get_job_context
from pydantic import Field

from call_reference import CallReferenceResult, CallReferenceTask
from dispatch import CallSpec
from prompts import END_CALL_DESCRIPTION, call_date_today, instructions
from topics import Topic, provider_out_of_network, required_topics

logger = logging.getLogger(__name__)

PROMPT = "verification"


class InterviewAgent(Agent):
    def __init__(
        self,
        spec: CallSpec,
        chat_ctx: llm.ChatContext | None = None,
        *,
        call_date: date | None = None,
        opening: str | None = None,
    ) -> None:
        # delete_room drops the representative's leg with the agent: without it the
        # session shuts down but the call stays up on a dead line. The SDK's tool is
        # kept for its shutdown mechanics but not exposed: end_call below wraps it
        # behind the reference gate.
        self._end_call_tool = EndCallTool(
            delete_room=True,
            end_instructions="Thank the representative for their time and say goodbye.",
        )
        # end_call below calls the SDK tool's private implementation, under a >=
        # dependency floor. Fail here, at construction before the dial, rather
        # than inside the hang-up with the line still up.
        if not callable(getattr(self._end_call_tool, "_end_call", None)):
            raise RuntimeError("livekit-agents EndCallTool no longer exposes _end_call")
        # chat_ctx is what the navigator hands over: the representative's greeting.
        # Without it the interview would open by asking who it is speaking with,
        # right after the rep said their name.
        super().__init__(instructions=instructions(PROMPT, spec), chat_ctx=chat_ctx)
        self._spec = spec
        # The first words to the representative, written by the navigator in the
        # same model call that decided a person answered (see navigator.py).
        self._opening = (opening or "").strip()
        # Enforce reference collection through a task before shutdown; a prompt
        # alone cannot guarantee the closing is complete.
        self._call_reference: CallReferenceResult | None = None
        self._closing = False
        # What the representative settled, by topic; end_call checks it so the
        # closing cannot start with a topic the call needs still unasked.
        self._answers: dict[Topic, str] = {}
        self._out_of_network: bool | None = None
        # One date for the whole call, captured at dial by the navigator: a call
        # that crosses midnight still composes the reference with the date the
        # payer logged it under.
        self._call_date = call_date or call_date_today()

    @property
    def call_reference(self) -> CallReferenceResult | None:
        return self._call_reference

    @property
    def call_date(self) -> date:
        return self._call_date

    @property
    def open_topics(self) -> list[Topic]:
        """The topics this call still needs, in the order the prompt asks them."""
        needed = required_topics(self._spec, self._out_of_network)
        return [topic for topic in needed if topic not in self._answers]

    @function_tool()
    async def record_answer(self, topic: Topic, answer: str) -> str:
        """Record what the representative said that settles one topic.

        Call it as soon as an answer settles a topic, asked or volunteered. A
        read-out that settles several topics gets one call per topic. A topic that
        does not apply, or that the representative says they cannot answer, is
        settled too: record that, e.g. "no copay" or "not provided". Record only
        what the representative said; never fill in an answer they did not give.
        The result names the next topic still open.

        Args:
            topic: The topic the answer settles.
            answer: The answer in a few words, as the representative gave it. For
                provider_network_status, say "in network" or "out of network".
        """
        answer = " ".join(answer.split())
        if not answer:
            raise ToolError("Empty answer: record what the representative said.")
        if topic == "provider_network_status":
            try:
                self._out_of_network = provider_out_of_network(answer)
            except ValueError:
                raise ToolError(
                    'Record the provider network status as "in network" or "out of '
                    'network"; if the representative has not said which, ask them.'
                ) from None
        self._answers[topic] = answer
        logger.info("Topic recorded: %s", topic)
        # A result, not None: the model records without speaking, and a silent
        # tool would end the turn in dead air. The reply that follows also knows
        # what is left, so the order survives a long call.
        if still_open := self.open_topics:
            return f"Recorded. Next open topic: {still_open[0]}."
        return "Recorded. Every topic is settled: call end_call."

    @function_tool()
    async def wait(self) -> None:
        """Stay silent as the only action when the line needs no reply from you.

        Choose silence when the representative needs time to look up information,
        type, finish speaking, or complete a hold or transfer. If a brief spoken
        acknowledgment is appropriate, say it and finish the response instead.
        Never combine wait with speech or another tool in the same turn.
        Finishing a spoken response already gives the representative the floor.
        Do not narrate silence.
        Questions, permission requests (including extending a hold), and
        presence checks need spoken answers, even during a wait. Resume
        verification when the representative is ready.
        """

    @function_tool(name="end_call", description=END_CALL_DESCRIPTION)
    async def end_call(
        self,
        ctx: RunContext,
        representative_cannot_continue: Annotated[
            bool,
            Field(
                description=(
                    "True only when the representative said the call cannot continue "
                    "or refused to help; the open topics are then left unasked."
                )
            ),
        ] = False,
    ) -> Any:
        if self._call_reference is None:
            if self._closing:
                # A second end_call in the same turn must not start a second task.
                raise ToolError("The closing is already in progress.")
            if not representative_cannot_continue and (still_open := self.open_topics):
                logger.info("Closing refused, topics open: %s", ", ".join(still_open))
                raise ToolError(
                    f"Not yet. Still open: {', '.join(still_open)}. Record any of these "
                    "the representative already answered with record_answer, then ask "
                    "about the first one still open. Call end_call again once every "
                    "topic is settled."
                )
            self._closing = True
            try:
                # Takes the floor until the representative confirms the read-back
                # or says no reference exists; the call cannot end without one.
                self._call_reference = await self._collect_call_reference(
                    ask_limitations=not representative_cannot_continue
                    and not self._benefits_skipped()
                )
            finally:
                self._closing = False
            self._stamp_report(self._call_reference)
        return await self._end_call_tool._end_call(ctx)

    def _benefits_skipped(self) -> bool:
        # The prompt's in-network-only rule: an out-of-network provider gets the
        # plan facts and no benefit questions, so no limitations question either.
        return (
            self._spec.network_benefit_scope == "in_network_only"
            and self._out_of_network is True
        )

    async def _collect_call_reference(
        self, *, ask_limitations: bool
    ) -> CallReferenceResult:
        return await CallReferenceTask(
            self._spec,
            call_date=self._call_date,
            ask_limitations=ask_limitations,
            chat_ctx=self.chat_ctx.copy(
                exclude_function_call=True, exclude_instructions=True
            ),
        )

    @staticmethod
    def _stamp_report(result: CallReferenceResult) -> None:
        # The confirmed values ride on the job context into the session report
        # (see report.py), the same way the answer time does. No job in tests.
        try:
            setattr(get_job_context(), "closing", asdict(result))
        except RuntimeError:
            pass

    async def on_enter(self) -> None:
        # The navigator writes the opening in the same model call that hands off,
        # so the representative hears it as soon as that call lands, with no model
        # call here. say() records it in the chat context, so the model continues
        # from the rep's reply instead of introducing itself again.
        #
        # Nothing to say means nobody was handed off to us: the agent was started
        # directly (tests, the console), where the other side speaks first and the
        # normal turn loop answers them. A handoff that somehow brings no opening
        # is that same silence, and the rep's next words start an ordinary turn.
        logger.info(
            "Interview entered; opening written at handoff: %s", bool(self._opening)
        )
        if self._opening:
            self.session.say(self._opening, add_to_chat_ctx=True)
