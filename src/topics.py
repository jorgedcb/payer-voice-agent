"""The benefit topics an interview must settle before it closes.

The verification prompt gives each topic its own `### N. Title` heading, and which
ones a call carries depends on the call spec (prior authorization on file, visit
limits, medical necessity, the network scope). They are read from the rendered
prompt, so the list the agent is told to work through and the list check_topics
asks about are the same text. The entrypoint reads them before dialing, so a prompt
edit that breaks the headings fails there rather than at the handoff.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard, get_args

_SECTION = re.compile(r"^## Benefits and plan facts$(.*?)^## Closing$", re.M | re.S)
_TOPIC = re.compile(r"^### \d+\. (.+)$", re.M)

Status = Literal["answered", "unavailable", "skipped", "open"]
STATUSES: tuple[Status, ...] = get_args(Status)


@dataclass(frozen=True)
class Topic:
    key: str
    title: str


def benefit_topics(prompt: str) -> tuple[Topic, ...]:
    section = _SECTION.search(prompt)
    if section is None:
        raise ValueError("the prompt has no benefit topics section")
    topics = tuple(
        Topic(key=re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"), title=title)
        for title in _TOPIC.findall(section.group(1))
    )
    if not topics or len({t.key for t in topics}) != len(topics):
        raise ValueError("benefit topics must be present and distinctly titled")
    return topics


def open_topics(topics: tuple[Topic, ...], marks: Mapping[str, object]) -> list[Topic]:
    """Topics not settled by their marks, in the prompt's order.

    A topic is settled when it is marked anything but open and the mark says why:
    the words from the line, or the fact on file or skip condition that applies.
    Anything else, a topic left out included, counts as open: the call stays on
    the benefits until the model shows otherwise for that topic.
    """
    return [t for t in topics if not _settled(marks.get(t.key))]


def unmarked(topics: tuple[Topic, ...], marks: Mapping[str, object]) -> list[Topic]:
    """Topics the check left without a valid status: the check was not filled in."""
    return [t for t in topics if not _has_status(marks.get(t.key))]


def _has_status(mark: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(mark, Mapping) and mark.get("status") in STATUSES


def _settled(mark: object) -> bool:
    if not _has_status(mark) or mark["status"] == "open":
        return False
    evidence = mark.get("evidence")
    return isinstance(evidence, str) and bool(evidence.strip())


def check_topics_schema(topics: tuple[Topic, ...]) -> dict[str, object]:
    return {
        "name": "check_topics",
        "description": (
            "Before closing, mark every benefit topic from the conversation so far. "
            "Call it when you think the last topic is settled, and before you ask "
            "anything about wrapping up; it says what to do next. Call it alone, "
            "with nothing spoken in that turn: the representative never hears "
            "about the check, only the question it leads to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                t.key: {
                    "type": "object",
                    "description": t.title,
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": list(STATUSES),
                            "description": (
                                "answered: stated on this call, by the representative "
                                "or the phone system's read-out. unavailable: the "
                                "representative said they cannot or will not give it, "
                                "or that it does not apply (for example, the plan is "
                                "not active). skipped: already on file, or its own "
                                "skip condition applies. open: not asked yet, or "
                                "asked and not answered."
                            ),
                        },
                        "evidence": {
                            "type": "string",
                            "description": (
                                "Briefly, for answered or unavailable, the words from "
                                "the line; for skipped, the fact on file or the "
                                "condition that applies. Empty for open."
                            ),
                        },
                    },
                    "required": ["status", "evidence"],
                    "additionalProperties": False,
                }
                for t in topics
            },
            "required": [t.key for t in topics],
            "additionalProperties": False,
        },
    }
