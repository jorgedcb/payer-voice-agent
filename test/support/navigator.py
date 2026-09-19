"""Assertions and turn helpers for navigator behavior."""

import json

from support.calls import called


async def hears(session, text: str):
    """One payer utterance, and the navigator's turn in response.

    Every test goes through here so one check runs on every turn: the agent
    produced no plain text. The navigator forces a tool call on each
    generation, so text should be impossible; if that guarantee ever breaks
    (an SDK change, a fallback provider that ignores "required") this fails
    on its own, rather than a test passing because the right words arrived
    through the wrong channel. Before the guarantee existed, asked to wait,
    the model said "Okay."
    """
    result = await session.run(user_input=text)
    stray = [
        ev.item.text_content
        for ev in result.events
        if ev.type == "message" and ev.item.role == "assistant" and ev.item.text_content
    ]
    assert not stray, f"navigator wrote text instead of calling a tool: {stray}"
    return result


def spoken(result) -> str:
    """The words the agent said this turn, through the `speak` tool."""
    return " ".join(
        json.loads(ev.item.arguments)["text"]
        for ev in result.events
        if ev.type == "function_call" and ev.item.name == "speak"
    ).strip()


def presses(result) -> list[list[str]]:
    """The key sequences the agent sent this turn, one list per tool call."""
    return [
        json.loads(ev.item.arguments)["events"]
        for ev in result.events
        if ev.type == "function_call" and ev.item.name == "send_dtmf_events"
    ]


def assert_moved_to_the_queue(result) -> None:
    """The phase switch is silent too: it says that the menu is behind us, no more.

    It is an agent handoff, so what it must not do is reach the interview: nobody
    has spoken yet.
    """
    assert spoken(result) == "", f"spoke instead of waiting: {spoken(result)!r}"
    assert presses(result) == [], f"pressed instead of waiting: {presses(result)}"
    assert not called(result, "representative_answered"), (
        "handed off to the interview with nobody on the line"
    )
    result.expect.contains_function_call(name="hold_for_representative")


def assert_waited(result) -> None:
    """Waiting is the `wait` tool and nothing else: no words, no keys, no handoff."""
    assert spoken(result) == "", f"spoke instead of waiting: {spoken(result)!r}"
    assert presses(result) == [], f"pressed instead of waiting: {presses(result)}"
    assert not any(ev.type == "agent_handoff" for ev in result.events)
    result.expect.contains_function_call(name="wait")
