"""The agent that talks to a live representative.

This is the third and final phase of a payer call. It is only ever reached by a
handoff from the navigator (see navigator.py), which carries over what the
representative already said, so this agent starts mid-conversation rather than
at silence.
"""

import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from livekit.agents import Agent, RunContext, ToolError, function_tool, llm
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.job import get_job_context

from call_reference import CallReferenceResult, CallReferenceTask
from dispatch import CallSpec
from prompts import END_CALL_DESCRIPTION, call_date_today, instructions

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
        # The call reference is the proof a verification happened. Prompt rules
        # alone let the model hang up on a goodbye about one turn in five, so
        # end_call collects it through CallReferenceTask before shutting down.
        self._call_reference: CallReferenceResult | None = None
        self._closing = False
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

    @function_tool()
    async def wait(self) -> None:
        """Say nothing this turn when the line needs no reply from you.

        Choose silence when the representative needs time to look up information,
        type, finish speaking, or complete a hold or transfer. A brief spoken
        acknowledgment is also valid when appropriate, with or without this tool.
        Do not narrate the silence.
        Questions, permission requests (including extending a hold), and
        presence checks need spoken answers, even during a wait. Resume
        verification when the representative is ready.
        """

    @function_tool(name="end_call", description=END_CALL_DESCRIPTION)
    async def end_call(self, ctx: RunContext) -> Any:
        if self._call_reference is None:
            if self._closing:
                # A second end_call in the same turn must not start a second task.
                raise ToolError("The closing is already in progress.")
            self._closing = True
            try:
                # Takes the floor until the representative confirms the read-back
                # or says no reference exists; the call cannot end without one.
                self._call_reference = await self._collect_call_reference()
            finally:
                self._closing = False
            self._stamp_report(self._call_reference)
        return await self._end_call_tool._end_call(ctx)

    async def _collect_call_reference(self) -> CallReferenceResult:
        return await CallReferenceTask(
            self._spec,
            call_date=self._call_date,
            chat_ctx=self.chat_ctx.copy(exclude_function_call=True, exclude_instructions=True),
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
        # call here. On one live call the rep waited 4.35s after asking our name,
        # said "Hello?" over the opening, and ended the call. say() records it in
        # the chat context, so the model continues from the rep's reply instead of
        # introducing itself again.
        #
        # Nothing to say means nobody was handed off to us: the agent was started
        # directly (tests, the console), where the other side speaks first and the
        # normal turn loop answers them. A handoff that somehow brings no opening
        # is that same silence, and the rep's next words start an ordinary turn.
        logger.info("Interview entered; opening written at handoff: %s", bool(self._opening))
        if self._opening:
            self.session.say(self._opening, add_to_chat_ctx=True)
