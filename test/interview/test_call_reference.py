"""The call reference is collected by a task that end_call runs before shutdown.

Offline: the task's tool mechanics and end_call's ordering. The live closing
scenarios are in test_closing.py.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from livekit.agents import ToolError, llm
from livekit.agents.beta.tools import EndCallTool

from call_reference import CallReferenceResult, CallReferenceTask
from dispatch import load_sample_spec
from interview import InterviewAgent

CALL_DATE = date(2026, 9, 12)


def make_task(*heard: str) -> CallReferenceTask:
    """The task with what the representative has said so far."""
    chat_ctx = llm.ChatContext()
    for text in heard:
        chat_ctx.add_message(role="user", content=text)
    return CallReferenceTask(load_sample_spec(), call_date=CALL_DATE, chat_ctx=chat_ctx)


def result(task: CallReferenceTask) -> CallReferenceResult:
    """The completed task's result. `await task` needs a live session, so this
    reads the SDK's future; keep the one private access here."""
    fut = task._AgentTask__fut  # type: ignore[attr-defined]
    assert fut.done()
    return fut.result()


def tool_names(task: CallReferenceTask) -> set[str]:
    return {tool.info.name for tool in task.tools}


async def test_confirm_tool_appears_only_after_a_value_is_recorded() -> None:
    task = make_task()
    assert tool_names(task) == {
        "update_representative_name",
        "update_call_reference",
        "no_reference_available",
    }

    reply = await task.update_call_reference("AB73921")

    assert task.current_reference == "AB73921"
    assert "confirm_call_reference" in tool_names(task)
    # Spelled for the model to copy: on its own it read "A, B, 7, 3, 9, 2, 1".
    assert (
        "The reference I have is A as in alpha, B as in bravo, seven, three, nine, two, one. Is that correct?"
        in reply
    )
    assert not task.done()


async def test_composed_reference_is_read_as_words_not_spelled() -> None:
    task = make_task("Jamie R.")
    await task.update_representative_name("Jamie", "R")
    reply = await task.update_call_reference("Jamie R 09/12/2026")
    assert "as in" not in reply
    assert "month named" in reply


async def test_a_code_spelled_in_groups_is_still_spelled_back() -> None:
    # Representatives read codes in groups and STT keeps the spaces; a space is
    # not a name and a date.
    task = make_task()
    reply = await task.update_call_reference("AB 73921")
    assert "A as in alpha, B as in bravo, seven, three, nine, two, one" in reply


async def test_confirmation_completes_with_the_reference_and_name() -> None:
    task = make_task("Jamie R.")
    await task.update_representative_name("Jamie", "R")
    await task.update_call_reference("AB73921")
    confirm = next(t for t in task.tools if t.info.name == "confirm_call_reference")

    assert await confirm() is None
    assert task.done()
    assert result(task) == CallReferenceResult(
        reference="AB73921", representative_name="Jamie R"
    )


async def test_confirmation_requires_the_name() -> None:
    task = make_task("Jamie R.")
    await task.update_call_reference("AB73921")
    confirm = next(t for t in task.tools if t.info.name == "confirm_call_reference")

    assert "update_representative_name" in (await confirm() or "")
    assert not task.done()

    await task.update_representative_name("Jamie", "R")
    assert await confirm() is None
    assert result(task).representative_name == "Jamie R"


async def test_no_reference_completes_without_a_name() -> None:
    # A representative who cannot verify and is hanging up is not held for it.
    task = make_task()
    await task.no_reference_available("We cannot provide one.")
    assert result(task).representative_name is None


async def test_first_name_alone_is_recorded_and_the_initial_asked_once() -> None:
    task = make_task("My name is Megan.")
    assert "last initial" in await task.update_representative_name("Megan")
    assert task.representative_name == "Megan"
    assert "last initial" not in await task.update_representative_name("Megan")
    assert task.representative_name == "Megan"


async def test_initial_given_later_completes_the_name() -> None:
    task = make_task("My name is Jamie.", "R as in Romeo.")
    await task.update_representative_name("Jamie")
    await task.update_representative_name("Jamie", " r .")
    assert task.representative_name == "Jamie R"


async def test_a_later_record_without_the_initial_keeps_it() -> None:
    # A spelling correction of the first name must not drop the initial or ask
    # for it again.
    task = make_task("Jamie R.", "It's Jaime, J A I M E.")
    await task.update_representative_name("Jamie", "R")
    reply = await task.update_representative_name("Jaime")
    assert task.representative_name == "Jaime R"
    assert "last initial" not in reply


@pytest.mark.parametrize(
    "heard",
    ["Megan S.", "Megan, S as in sierra.", "It's Megan Smith.", "Last initial s."],
)
async def test_an_initial_the_line_gave_is_recorded(heard: str) -> None:
    task = make_task(heard)
    await task.update_representative_name("Megan", "S")
    assert task.representative_name == "Megan S"


@pytest.mark.parametrize("heard", ["My name is Megan.", "It's Megan, how's it going?"])
async def test_an_initial_the_line_never_gave_is_refused(heard: str) -> None:
    # The real failure: the model filled a missing initial with the caller's own.
    task = make_task(heard)
    with pytest.raises(ToolError, match="has not said S"):
        await task.update_representative_name("Megan", "S")
    assert task.representative_name is None


async def test_an_accented_initial_is_kept() -> None:
    task = make_task("Soy Ana Ñ.")
    await task.update_representative_name("Ana", "ñ")
    assert task.representative_name == "Ana Ñ"


@pytest.mark.parametrize(
    ("first_name", "last_initial"),
    [(" ", None), ("R", None), ("Jamie R", None), ("Jamie", "Ro"), ("Jamie", "7")],
)
async def test_invalid_name_is_refused(
    first_name: str, last_initial: str | None
) -> None:
    task = make_task("Jamie R.")
    with pytest.raises(ToolError):
        await task.update_representative_name(first_name, last_initial)
    assert task.representative_name is None


async def test_composed_reference_cannot_add_an_initial() -> None:
    task = make_task("My name is Megan.", "The reference is my name and today's date.")
    await task.update_representative_name("Megan")
    reply = await task.update_call_reference("Megan S 09/12/2026")
    assert task.current_reference == "Megan 09/12/2026"
    assert '"Megan 09/12/2026"' in reply


async def test_composed_reference_keeps_the_initial_given() -> None:
    task = make_task("Jamie R.", "The reference is my name and today's date.")
    await task.update_representative_name("Jamie", "R")
    await task.update_call_reference("Jamie 09/12/2026")
    assert task.current_reference == "Jamie R 09/12/2026"


async def test_composed_reference_names_the_representative() -> None:
    task = make_task("Jamie R.", "The reference is my name and today's date.")
    await task.update_representative_name("Jamie", "R")
    with pytest.raises(ToolError, match='"Jamie R 09/12/2026"'):
        await task.update_call_reference("Alex 09/12/2026")
    assert task.current_reference is None


async def test_declined_name_does_not_hold_the_call() -> None:
    # The gate sends the model back for the name once; a representative who
    # will not give it must not keep the line open, so the next confirm ends.
    task = make_task()
    await task.update_call_reference("AB73921")
    confirm = next(t for t in task.tools if t.info.name == "confirm_call_reference")
    assert "update_representative_name" in (await confirm() or "")
    assert await confirm() is None
    assert result(task) == CallReferenceResult(reference="AB73921")


async def test_prompt_carries_the_call_date_and_disclosure() -> None:
    text = make_task().instructions or ""
    assert "09/12/2026" in text
    assert "virtual assistant" in text
    # ElevenLabs reads markup aloud (a live call spoke "expr type prosody"), so the
    # read-back is paced with plain words, never a tag.
    assert "one character at a" in text
    assert "<" not in text


async def test_stale_confirmation_after_a_correction_is_rejected() -> None:
    task = make_task()
    await task.update_call_reference("AB73921")
    stale = next(t for t in task.tools if t.info.name == "confirm_call_reference")
    await task.update_call_reference("AB73927")

    assert "changed" in (await stale() or "")
    assert not task.done()


async def test_empty_reference_is_refused() -> None:
    task = make_task()
    with pytest.raises(ToolError):
        await task.update_call_reference("  ")
    assert "confirm_call_reference" not in tool_names(task)


async def test_no_reference_completes_with_the_statement() -> None:
    task = make_task()
    await task.no_reference_available(
        "We do not issue reference numbers for eligibility calls."
    )
    assert result(task) == CallReferenceResult(
        reference=None,
        unavailable_statement="We do not issue reference numbers for eligibility calls.",
    )


async def test_end_call_collects_the_reference_before_shutting_down() -> None:
    agent = InterviewAgent(spec=load_sample_spec())
    order: list[str] = []

    async def collect() -> CallReferenceResult:
        order.append("collect")
        return CallReferenceResult(reference="AB73921")

    async def shutdown(ctx) -> str:
        order.append("shutdown")
        return "bye"

    agent._end_call_tool._end_call = AsyncMock(side_effect=shutdown)
    with patch.object(InterviewAgent, "_collect_call_reference", side_effect=collect):
        assert await agent.end_call(MagicMock(), reason="call_cannot_continue") == "bye"

    assert order == ["collect", "shutdown"]
    assert agent.call_reference == CallReferenceResult(reference="AB73921")


async def test_end_call_does_not_shut_down_if_collection_fails() -> None:
    agent = InterviewAgent(spec=load_sample_spec())
    shutdown = AsyncMock()
    agent._end_call_tool._end_call = shutdown
    with (
        patch.object(
            InterviewAgent,
            "_collect_call_reference",
            side_effect=ToolError("cancelled"),
        ),
        pytest.raises(ToolError),
    ):
        await agent.end_call(MagicMock(), reason="call_cannot_continue")
    shutdown.assert_not_awaited()
    assert agent.call_reference is None


def test_interview_exposes_wait_topic_check_and_gated_end_call() -> None:
    agent = InterviewAgent(spec=load_sample_spec())
    # Reference collection still belongs to the closing task; the interview
    # can wait, check its topics, or enter closing through its guarded end_call.
    assert {tool.info.name for tool in agent.tools} == {
        "wait",
        "check_topics",
        "end_call",
    }


async def test_task_gets_the_interview_call_date() -> None:
    # The date is fixed at dial (navigator) and travels to the task unchanged,
    # so a call crossing midnight still composes the payer's date.
    agent = InterviewAgent(spec=load_sample_spec(), call_date=CALL_DATE)
    with patch("interview.CallReferenceTask") as task_cls:
        task_cls.return_value = AsyncMock(return_value=CallReferenceResult("AB1"))()
        await agent._collect_call_reference()
    assert task_cls.call_args.kwargs["call_date"] == CALL_DATE


async def test_second_end_call_while_closing_is_refused() -> None:
    agent = InterviewAgent(spec=load_sample_spec())
    agent._end_call_tool._end_call = AsyncMock(return_value="bye")
    started = MagicMock()

    async def slow_collect() -> CallReferenceResult:
        started()
        with pytest.raises(ToolError, match="already in progress"):
            await agent.end_call(MagicMock(), reason="call_cannot_continue")
        return CallReferenceResult(reference="AB73921")

    with patch.object(
        InterviewAgent, "_collect_call_reference", side_effect=slow_collect
    ):
        assert await agent.end_call(MagicMock(), reason="call_cannot_continue") == "bye"
    started.assert_called_once()


def test_sdk_end_call_tool_still_has_the_private_hangup() -> None:
    # interview.end_call calls it; the constructor also checks, before dialing.
    assert callable(getattr(EndCallTool(delete_room=True), "_end_call", None))
