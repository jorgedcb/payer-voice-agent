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
from prompts.formatting import NATO_ALPHABET, speak_code

logger = logging.getLogger(__name__)

PROMPT = "call_reference"

_COMPOSED_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def _single_letter(word: str) -> bool:
    return len(word) == 1 and word.isalpha()


def _name_words(name: str) -> list[str]:
    """A name's words, without the case and punctuation speech recognition varies."""
    return re.sub(r"[.,]", " ", name).upper().split()


def _said_initial(heard: str, first_name: str, initial: str) -> bool:
    """Whether the line's words give this initial: as a letter on its own ("S",
    "S."), its phonetic word ("sierra"), or a last name starting with it after
    the first name ("Megan Smith"). An apostrophe's "s" is not a letter said."""
    letter = re.escape(initial)
    word = NATO_ALPHABET.get(initial.lower())
    return bool(
        re.search(rf"(?<![\w'’]){letter}(?![\w'’])", heard, re.I)
        or (word and re.search(rf"\b{word}\b", heard, re.I))
        or re.search(rf"\b{re.escape(first_name)}[,.]?\s+{letter}\w", heard, re.I)
    )


@dataclass
class CallReferenceResult:
    reference: str | None
    """The reference as confirmed, or None when the representative said none can be provided."""

    unavailable_statement: str | None = None
    """The representative's words when no reference can be provided."""

    representative_name: str | None = None
    """The name as the representative gave it: "Jamie R", or "Jamie" without an initial."""


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
        self._first_name: str | None = None
        self._initial: str | None = None
        self._initial_asked = False
        self._name_asked_at_confirm = False

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Ask for the representative's name: first name and last initial. Anything they "
                "already gave earlier in this conversation, record with the matching tool "
                "instead of asking again, and move on to what is still missing."
            )
        )

    @property
    def current_reference(self) -> str | None:
        return self._current

    @property
    def representative_name(self) -> str | None:
        if self._first_name is None:
            return None
        return " ".join(filter(None, (self._first_name, self._initial)))

    def _heard(self) -> str:
        """Everything the line has said in this conversation."""
        return "\n".join(
            item.text_content or ""
            for item in self.chat_ctx.items
            if item.type == "message" and item.role == "user"
        )

    @function_tool()
    async def update_representative_name(
        self, first_name: str, last_initial: str | None = None
    ) -> str:
        """Record the representative's name as they gave it.

        Args:
            first_name: Their first name only, e.g. "Jamie".
            last_initial: The letter of their last name they said, e.g. "R". Leave it
                out when they have not said one; an initial recorded earlier is kept.
        """
        words = first_name.split()
        if not words:
            raise ToolError("Empty name. Ask the representative for their first name.")
        if _single_letter(words[-1]):
            raise ToolError(
                "first_name takes the first name only; pass a last initial the "
                "representative said in last_initial."
            )
        initial = re.sub(r"[\s.]", "", last_initial or "").upper()
        if initial and not _single_letter(initial):
            raise ToolError(
                "last_initial is one letter. Leave it out if the representative did "
                "not give one."
            )
        name = " ".join(words)
        # The model has filled a missing initial with its own (a real call): an
        # initial counts only when the line's words give it.
        if initial and not _said_initial(self._heard(), name, initial):
            raise ToolError(
                f"The representative has not said {initial} as their last initial. "
                "Record the first name alone unless they give an initial."
            )
        self._first_name = name
        if initial:
            self._initial = initial
        logger.info("Representative name recorded")
        if self._initial or self._initial_asked:
            return "Recorded. Now the call reference, if you do not have it yet."
        # A first name alone is a complete record; the initial is asked for once.
        self._initial_asked = True
        return (
            "Recorded. If you have not asked for their last initial, ask for it once, "
            "together with the call reference if you do not have that yet. Record the "
            "name again only if they give the initial."
        )

    @function_tool()
    async def update_call_reference(self, reference: str) -> str:
        """Record the call reference exactly as the representative gave it.

        Args:
            reference: The complete reference, e.g. "AB73921", or the composed value
                only when the representative said the reference is their name and the
                date: the name as recorded, then the date in digits, e.g.
                "Jamie 09/12/2026". Never a composed value while they look one up.
        """
        reference = reference.strip()
        if not reference:
            raise ToolError(
                "Empty reference. Ask the representative for the call reference."
            )
        if date_found := _COMPOSED_DATE.search(reference):
            # A composed reference repeats the name, and it is the name as recorded:
            # the model has both added an initial nobody said and dropped one that
            # was said, so the recorded one replaces whatever initial it wrote.
            name_part = _name_words(reference[: date_found.start()])
            if self._first_name is not None:
                if [w for w in name_part if not _single_letter(w)] != _name_words(
                    self._first_name
                ):
                    raise ToolError(
                        "The name in a composed reference is the representative's name "
                        f'as recorded. Call update_call_reference with "'
                        f'{self.representative_name} {date_found.group()}".'
                    )
                reference = f"{self.representative_name} {date_found.group()}"
            elif any(_single_letter(w) for w in name_part):
                raise ToolError(
                    "The name in a composed reference is the representative's name as "
                    "recorded with update_representative_name, with no other initial."
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
                f'The reference is "{reference}". Read it back with the name said '
                "naturally and the date with the month named, then ask whether it is "
                "correct."
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
            if self._first_name is None and not self._name_asked_at_confirm:
                # The model sometimes answers in text without recording the name
                # it was just given; the completion gate sends it back for it.
                # Once only: a representative who declines to give a name must
                # not hold the call open, so the second confirm goes through.
                self._name_asked_at_confirm = True
                return (
                    "Not confirmed yet: record the representative's name as they gave it "
                    "with update_representative_name first (ask for it if they have not "
                    "given it), then call confirm_call_reference again."
                )
            if not self.done():
                logger.info("Call reference confirmed")
                self.complete(
                    CallReferenceResult(
                        reference=reference,
                        representative_name=self.representative_name,
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
                    representative_name=self.representative_name,
                )
            )
