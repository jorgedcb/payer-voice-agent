"""Two phases before the interview: the menu's long wait, then the queue's short one.

Which phase the call is in is something the model says out loud by calling
`hold_for_representative`, so these tests pin both halves -- what the switch
carries forward, and whether the model calls it when a payer line transfers.
"""

from unittest.mock import MagicMock

import pytest
from livekit.agents import llm
from livekit.agents.llm.tool_context import get_fnc_tool_names

from calls import (
    AETNA_CALL,
    AETNA_PATIENT,
    called,
    handoff_opening,
    replay,
    show_response,
    turn_settings,
)
from dispatch import sample_spec
from interview import InterviewAgent
from navigator import HOLD_TURN_HANDLING, MENU_TURN_HANDLING, HoldAgent, NavigatorAgent
from prompts import call_date_today

TRANSFER = (
    "Thank you. Please hold while I transfer you to the next available representative. "
    "Your estimated wait time is four minutes."
)
MENU = (
    "Coverage and benefits. Say claims, precertification, contact information, or "
    "appeal inquiries."
)


def a_navigator() -> NavigatorAgent:
    return NavigatorAgent(spec=sample_spec(), llm_model=MagicMock(spec=llm.LLM))


def a_hold_agent() -> HoldAgent:
    return HoldAgent(
        spec=sample_spec(), call_date=call_date_today(), llm_model=MagicMock(spec=llm.LLM)
    )


def test_only_the_menu_sets_its_own_wait() -> None:
    # The menu waits out the pause between options. The queue sets no endpointing
    # at all, so it inherits the session's -- the wait the interview runs on.
    assert MENU_TURN_HANDLING["endpointing"]["min_delay"] == 1.5
    assert "endpointing" not in HOLD_TURN_HANDLING
    # Speculative generation is thrown away by a menu that keeps talking, and
    # nothing talks over hold music.
    assert MENU_TURN_HANDLING["preemptive_generation"]["enabled"] is False
    assert HOLD_TURN_HANDLING["preemptive_generation"]["enabled"] is True


def test_the_queue_cannot_put_itself_in_the_queue_again() -> None:
    menu_tools = set(get_fnc_tool_names(a_navigator().tools))
    hold_tools = set(get_fnc_tool_names(a_hold_agent().tools))
    assert "hold_for_representative" in menu_tools
    assert "hold_for_representative" not in hold_tools
    # Everything else survives the switch: a line that transfers and then asks for
    # the member ID again is still answerable.
    assert menu_tools - hold_tools == {"hold_for_representative"}


async def test_the_switch_carries_the_call_into_the_queue() -> None:
    navigator = a_navigator()
    await replay(navigator, AETNA_CALL)

    hold = await navigator.hold_for_representative()

    assert isinstance(hold, HoldAgent)
    # The same call, mid-conversation: the specification, the dial-time date and
    # the opening's wording nudge, which must not change with the phase.
    assert hold._spec == navigator._spec
    assert hold._call_date == navigator._call_date
    assert [item.id for item in hold.chat_ctx.items] == [
        item.id for item in navigator.chat_ctx.items
    ]
    # The prompt changes with the phase: hold music and pickups need no rules of
    # their own (the `wait` tool and ACTIONS already cover them), but the queue is
    # not told how to put itself in the queue again.
    assert "hold_for_representative" in navigator.instructions
    assert "hold_for_representative" not in hold.instructions
    assert navigator._spec.member_id in hold.instructions


async def test_the_queue_hands_off_with_the_opening() -> None:
    hold = a_hold_agent()

    interview = await hold.representative_answered(opening="Yes, hi. This is Jordan.")

    assert isinstance(interview, InterviewAgent)
    assert interview._opening == "Yes, hi. This is Jordan."


async def test_a_transfer_announcement_moves_the_call_to_the_queue(
    session, start_navigator
) -> None:
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)

    result = await session.run(user_input=TRANSFER)
    show_response(TRANSFER, result)

    result.expect.next_event().is_function_call(name="hold_for_representative")
    result.expect.next_event().is_function_call_output()
    result.expect.next_event().is_agent_handoff(new_agent_type=HoldAgent)
    assert isinstance(session.current_agent, HoldAgent)


@pytest.mark.parametrize("still_the_menu", [
    MENU,
    "One moment, please. The patient is Rashid Amari. Correct?",
    "For eligibility and benefits, press one. For claim status, press two.",
])
async def test_a_menu_still_asking_stays_in_the_menu(
    session, start_navigator, still_the_menu
) -> None:
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)

    result = await session.run(user_input=still_the_menu)
    show_response(still_the_menu, result)

    assert not called(result, "hold_for_representative"), "the menu is still asking"
    assert isinstance(session.current_agent, NavigatorAgent)


async def test_the_queue_runs_on_the_interviews_timing(session, start_navigator) -> None:
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)

    endpointing, preemptive = turn_settings(session)
    assert endpointing["min_delay"] == 1.5, "the menu waits out the pause between options"
    assert preemptive is False

    await session.run(user_input=TRANSFER)

    # The queue is the interview's timing, one phase early: the session's wait,
    # and the model started before the turn is confirmed.
    endpointing, preemptive = turn_settings(session)
    assert endpointing == session.options.endpointing
    assert preemptive is True


async def test_hold_music_in_the_queue_is_waited_out(session, start_navigator) -> None:
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)
    await session.run(user_input=TRANSFER)

    announcement = (
        "Your call is important to us. Please stay on the line and a representative "
        "will be with you shortly."
    )
    result = await session.run(user_input=announcement)
    show_response(announcement, result)

    result.expect.contains_function_call(name="wait")
    assert not called(result, "representative_answered"), "nobody has spoken yet"
    assert not called(result, "speak"), "never speak into hold music"


async def test_a_menu_after_the_transfer_is_still_answered(session, start_navigator) -> None:
    # The safety valve for calling the switch early: a line that announces a
    # transfer and then wants a value keeps every tool it had.
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)
    await session.run(user_input=TRANSFER)
    assert isinstance(session.current_agent, HoldAgent)

    relapse = "Before I connect you, please enter or say the patient's member ID."
    result = await session.run(user_input=relapse)
    show_response(relapse, result)

    assert called(result, "speak") or called(result, "send_dtmf_events"), "the line asked again"
    assert not called(result, "representative_answered")


async def test_the_queue_hands_off_when_a_person_picks_up(session, start_navigator) -> None:
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)
    await session.run(user_input=TRANSFER)
    assert isinstance(session.current_agent, HoldAgent)

    pickup = "Thank you for holding. This is Wilson. May I have your name, please?"
    result = await session.run(user_input=pickup)
    show_response(pickup, result)

    assert handoff_opening(result).strip()
    result.expect.next_event().is_function_call_output()
    result.expect.next_event().is_agent_handoff(new_agent_type=InterviewAgent)
