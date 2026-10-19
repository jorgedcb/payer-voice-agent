"""Close the call: the representative's name, then the call reference.

Ending the call runs this task first (see InterviewAgent.end_call) so reference
collection is enforced before shutdown. It takes the floor,
gets the representative's name and the reference, reads the reference back,
and returns only once the representative confirms it or explicitly says none
can be provided.

Shape follows the SDK's prebuilt tasks (livekit.agents.beta.workflows): the
confirm tool exists only after a value was recorded, so the read-back cannot be
skipped in the same turn. Its script is prompts/call_reference.md, rendered
like the other prompts and validated before dialing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from livekit.agents import (
    NOT_GIVEN,
    AgentTask,
    NotGivenOr,
    ToolError,
    function_tool,
    llm,
)

from dispatch import CallSpec
from prompts import instructions
from prompts.formatting import speak_code

logger = logging.getLogger(__name__)

PROMPT = "call_reference"

_COMPOSED_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


@dataclass
class CallReferenceResult:
    reference: str | None
    """The reference as confirmed, or None when the representative said none can be provided."""

    unavailable_statement: str | None = None
    """The representative's words when no reference can be provided."""

    representative_name: str | None = None
    """First name and last initial, e.g. "Jamie R", when the representative gave it."""


class CallReferenceTask(AgentTask[CallReferenceResult]):
    def __init__(
        self,
        spec: CallSpec,
        *,
        call_date: date,
        chat_ctx: NotGivenOr[llm.ChatContext] = NOT_GIVEN,
    ) -> None:
        super().__init__(
            instructions=instructions(PROMPT, spec, call_date=call_date),
            chat_ctx=chat_ctx,
        )
        self._current: str | None = None
        self._name: str | None = None
        self._name_asked_at_confirm = False

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Ask for the representative's first name and last initial. Anything they "
                "already gave earlier in this conversation, record with the matching tool "
                "instead of asking again, and move on to what is still missing."
            )
        )

    @property
    def current_reference(self) -> str | None:
        return self._current

    @property
    def representative_name(self) -> str | None:
        return self._name

    @function_tool()
    async def update_representative_name(self, name: str) -> str:
        """Record the representative's first name and last initial.

        Args:
            name: First name and last initial as given, e.g. "Jamie R".
        """
        name = " ".join(name.split())
        if not re.fullmatch(r"\S+( \S+)+", name):
            raise ToolError(
                "Incomplete name: record the first name and the last initial together, "
                'e.g. "Jamie R". Ask for whichever part is missing.'
            )
        self._name = name
        logger.info("Representative name recorded")
        return "Recorded. Now the call reference, if you do not have it yet."

    @function_tool()
    async def update_call_reference(self, reference: str) -> str:
        """Record the call reference exactly as the representative gave it.

        Args:
            reference: The complete reference, e.g. "AB73921", or the composed value
                when the representative said the reference is their name and the date,
                the date in digits, e.g. "Jamie R 09/12/2026".
        """
        reference = reference.strip()
        if not reference:
            raise ToolError(
                "Empty reference. Ask the representative for the call reference."
            )
        self._current = reference
        # The confirm tool appears only now, so a confirmation cannot be claimed in
        # the same turn the value was heard: the read-back has to happen first.
        confirm = self._build_confirm_tool(reference)
        await self.update_tools(
            [t for t in self.tools if t.id != confirm.id] + [confirm]
        )
        logger.info("Call reference recorded, awaiting confirmation")
        # Supply code spelling directly so the voice gets digit words and NATO
        # letters. A composed name/date reference is read naturally; detect it by
        # its date because grouped codes can also contain spaces.
        if _COMPOSED_DATE.search(reference):
            read_back = (
                "Read it back with the name said naturally and the date with the month "
                "named, then ask whether it is correct."
            )
        else:
            read_back = (
                "Say this to the representative word for word, keeping every 'as in' "
                f'phrase: "The reference I have is {speak_code(reference)}. Is that correct?"'
            )
        return f"Recorded. {read_back} Do not call confirm_call_reference until they confirm."

    def _build_confirm_tool(self, reference: str) -> llm.FunctionTool:
        @function_tool()
        async def confirm_call_reference() -> str | None:
            """Call only after the representative confirms the read-back is correct."""
            if reference != self._current:
                return (
                    "The reference changed since the read-back. Read the current one back "
                    "and ask the representative to confirm it."
                )
            if self._name is None and not self._name_asked_at_confirm:
                # The model sometimes answers in text without recording the name
                # it was just given; the completion gate sends it back for it.
                # Once only: a representative who declines to give a name must
                # not hold the call open, so the second confirm goes through.
                self._name_asked_at_confirm = True
                return (
                    "Not confirmed yet: record the representative's first name and last "
                    "initial with update_representative_name first (ask for it if they "
                    "have not given it), then call confirm_call_reference again."
                )
            if not self.done():
                logger.info("Call reference confirmed")
                self.complete(
                    CallReferenceResult(
                        reference=reference, representative_name=self._name
                    )
                )
            return None

        return confirm_call_reference

    @function_tool()
    async def no_reference_available(self, statement: str) -> None:
        """Only when the representative explicitly states that no reference can be provided.

        A reference still being generated, a goodbye, or "anything else?" is not that.

        Args:
            statement: The representative's words saying no reference can be provided.
        """
        if not self.done():
            logger.info("Representative stated no reference is available")
            self.complete(
                CallReferenceResult(
                    reference=None,
                    unavailable_statement=statement,
                    representative_name=self._name,
                )
            )
