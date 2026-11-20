"""The agent that talks to a live representative.

This is the third and final phase of a payer call. It is only ever reached by a
handoff from the navigator (see navigator.py), which carries over what the
representative already said, so this agent starts mid-conversation rather than
at silence.
"""

import logging
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from typing import Any, Literal

from livekit.agents import Agent, RunContext, ToolError, function_tool, llm
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.job import get_job_context

from call_reference import CallReferenceResult, CallReferenceTask
from dispatch import CallSpec
from prompts import END_CALL_DESCRIPTION, call_date_today, instructions
from topics import (
    Topic,
    benefit_topics,
    check_topics_schema,
    open_topics,
    unmarked,
)

logger = logging.getLogger(__name__)

PROMPT = "verification"

CLOSING_QUESTION = (
    "Every topic is settled. Ask whether there are any other limitations or "
    "exclusions. Once that is answered, call end_call with reason "
    "verification_complete."
)
CLOSING_ASKED = (
    "Every topic is settled and the closing question was already asked. Once the "
    "representative has answered it, call end_call with reason verification_complete."
)

EndReason = Literal["verification_complete", "call_cannot_continue"]


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
        prompt = instructions(PROMPT, spec)
        # The closing question comes from check_topics, and end_call will not end
        # a finished verification before a check has found nothing open: calls
        # closed early with topics never asked even with the prompt's own rule to
        # check the list first.
        self._topics = benefit_topics(prompt)
        # None until the first check; the topics the last check left open after.
        self._open_topics: list[Topic] | None = None
        # The representative turns heard when the last check passed, and when a
        # finished verification was last refused: the closing question needs an
        # answer after the first, and a refusal cannot be turned into "the call
        # cannot continue" before the line has said anything new.
        self._passed_at: int | None = None
        self._refused_at: int | None = None
        # The turn whose ending was held once for the closing question.
        self._held_for_question_at: int | None = None
        check_tool = function_tool(
            self._check_topics_tool, raw_schema=check_topics_schema(self._topics)
        )
        super().__init__(instructions=prompt, chat_ctx=chat_ctx, tools=[check_tool])
        self._spec = spec
        # The first words to the representative, written by the navigator in the
        # same model call that decided a person answered (see navigator.py).
        self._opening = (opening or "").strip()
        # Enforce reference collection through a task before shutdown; a prompt
        # alone cannot guarantee the closing is complete.
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

    @property
    def topics(self) -> tuple[Topic, ...]:
        return self._topics

    def _representative_turns(self) -> int:
        return sum(
            1
            for item in self.chat_ctx.items
            if item.type == "message" and item.role == "user"
        )

    def check_topics(self, marks: Mapping[str, object]) -> str:
        """Record the model's marks and say what comes next."""
        if missing := unmarked(self._topics, marks):
            # A check with no statuses says nothing about the call; it must not
            # send the model back to topics that were answered.
            raise ToolError(
                "Each topic needs a status (answered, unavailable, skipped or open) "
                f"and its evidence. Missing for: {', '.join(t.key for t in missing)}. "
                "Call check_topics again with every topic marked, before saying anything."
            )
        self._open_topics = open_topics(self._topics, marks)
        # Topic keys only: titles are prompt text, never call data.
        logger.info(
            "Topic check: open %s", [t.key for t in self._open_topics] or "none"
        )
        if not self._open_topics:
            turns = self._representative_turns()
            if self._passed_at is not None and turns > self._passed_at:
                return CLOSING_ASKED
            self._passed_at = turns
            return CLOSING_QUESTION
        self._passed_at = None
        first, *rest = self._open_topics
        also = (
            f" Still open after that: {', '.join(t.title for t in rest)}."
            if rest
            else ""
        )
        return (
            f"Not every topic is settled. Ask about {first.title} now, as its own "
            f"question.{also} Call check_topics again once you think the rest are settled."
        )

    async def _check_topics_tool(self, raw_arguments: dict[str, object]) -> str:
        return self.check_topics(raw_arguments)

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
    async def end_call(self, ctx: RunContext, reason: EndReason) -> Any:
        """
        Args:
            reason: verification_complete once check_topics found every topic
                settled and the closing question is answered; call_cannot_continue
                when the representative refuses to help, cannot verify, or says the
                call cannot go on.
        """
        logger.info(
            "end_call: %s; open at last check: %s",
            reason,
            "unchecked"
            if self._open_topics is None
            else [t.key for t in self._open_topics] or "none",
        )
        self._gate(reason)
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
            self._stamp_report(self._call_reference, reason)
        return await self._end_call_tool._end_call(ctx)

    def _gate(self, reason: EndReason) -> None:
        """Refuse an ending the conversation does not support yet."""
        turns = self._representative_turns()
        if reason == "call_cannot_continue":
            if self._refused_at is not None and turns <= self._refused_at:
                raise ToolError(
                    "Nothing new was said since ending was refused for open benefit "
                    "topics. Ask about the first open topic; end the call only if the "
                    "representative then says it cannot go on."
                )
            return
        if self._open_topics is None:
            message = (
                "The benefit topics have not been checked. Call check_topics before "
                "ending the call."
            )
        elif self._open_topics:
            # Not naming the topic: it may have been answered since that check.
            message = (
                "Benefit topics were still open at the last check. Call check_topics "
                "again, marking each topic from the conversation, before ending the call."
            )
        elif (
            self._passed_at is not None
            and turns <= self._passed_at
            and self._held_for_question_at != turns
        ):
            # Ending in the same turn the check passed: in one response with it,
            # or with the closing question still unanswered. Held once only: the
            # model may have asked and heard the question before checking.
            self._held_for_question_at = turns
            message = (
                "Not ended yet. If you have not asked whether there are any other "
                "limitations or exclusions, ask exactly that now, as your whole reply, "
                "and wait for the answer. If the representative already answered it, "
                "call end_call again now, before saying anything."
            )
        else:
            return
        self._refused_at = turns
        raise ToolError(message)

    async def _collect_call_reference(self) -> CallReferenceResult:
        return await CallReferenceTask(
            self._spec,
            call_date=self._call_date,
            chat_ctx=self.chat_ctx.copy(
                exclude_function_call=True, exclude_instructions=True
            ),
        )

    @staticmethod
    def _stamp_report(result: CallReferenceResult, reason: EndReason) -> None:
        # The confirmed values ride on the job context into the session report
        # (see report.py), the same way the answer time does. No job in tests.
        # The reason tells a finished verification from a call that was cut off.
        try:
            setattr(get_job_context(), "closing", asdict(result) | {"reason": reason})
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
