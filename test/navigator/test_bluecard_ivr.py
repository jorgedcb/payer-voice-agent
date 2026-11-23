"""BlueCard's eligibility line: one phone system that transfers to another.

BlueCard asks for the member ID's prefix and transfers the call to the member's
own Blue plan, where that plan's phone system answers with a recorded greeting
and a menu of its own. On a 2026-11-20 call to a Michigan Blue plan, the agent
answered that menu and then took its next prompt, "In a few words, how can I
help you?", for a representative: BlueCard had said it was connecting the call,
and a voice after a transfer was read as a person. The interview then spent
two and a half minutes reading numbers aloud to a system that wanted them
keyed.

The lines are from that call; identifiers are synthetic.
"""

import re

import pytest
from support.calls import called, replay, show_response, spoken_digits
from support.navigator import (
    asks_for_a_person,
    assert_moved_to_the_queue,
    assert_waited,
    hears,
    keyed,
    presses,
    spoken,
    states_the_reason,
)

from interview import InterviewAgent
from navigator import NavigatorAgent

PATIENT = {
    "member_name": "Theo Lindqvist",
    "member_id": "ZGP604218375",
    "member_date_of_birth": "2016-03-08",
    # Fails the NPI check digit, so it cannot be anyone's.
    "npi": "1659302785",
    "service_type": "physical_therapy",
}
NPI = PATIENT["npi"]

BLUECARD_GREETING = (
    "Thank you for calling Blue Card eligibility. If this is a medical emergency, "
    "please hang up and call nine one one. Calls may be monitored or recorded."
)
PROVIDER_QUESTION = (
    "This line is for health care providers to determine a patient's benefit "
    "eligibility and precertification information. For anything else, please call "
    "the number on the back of the ID card. To locate a provider, please visit w w w "
    "dot b c b s dot com. Am I speaking with a health care provider?"
)
FEP_QUESTION = "Does your member ID begin with the letter r followed by numbers."
PREFIX_QUESTION = (
    "What are the first three characters of the patient's member ID? You can tell me "
    "the letters or use the phonetic alphabet, such as foxtrot Zulu Juliet four f d j."
)
PREFIX_READBACK = "That was z g p. Is that correct?"
REASON_MENU = "Are you calling for precertification, benefit in eligibility, or both?"
TO_THE_PLAN = (
    "Thank you. Please hold while your call is being connected to the appropriate "
    "Blue Cross Blue Shield plan."
)

PLAN_GREETING = (
    "Hello. You've reached Blue Cross Blue Shield and Blue Care Network of Michigan. "
    "Your call may be monitored and recorded for quality evaluation, training, and "
    "identification purposes. If you bill on a CMS fifteen hundred claim form, say "
    "professional. If you bill on a u b zero four claim form, say facility. You can "
    "also say vision or hearing."
)
PLAN_HOW_CAN_I_HELP = "In a few words, how can I help you?"
PLAN_READBACK = "You're asking about eligibility. Is that right?"
PLAN_NPI_QUESTION = (
    "Following HIPAA regulations, please provide a valid authentication "
    "identification number. Please tell me the Blue Cross Blue Shield of Michigan "
    "issued provider identification number or the national provider identification "
    "number."
)
# Not from the call, which never got this far: what a plan says once it has
# what it needs.
PLAN_HOLD = (
    "Thank you. Please hold for the next available representative. Your call "
    "will be answered in the order it was received."
)

# BlueCard, the way the agent took it on the call.
THROUGH_BLUECARD = [
    ("user", BLUECARD_GREETING),
    ("wait", None),
    ("user", PROVIDER_QUESTION),
    ("speak", "yes"),
    ("user", FEP_QUESTION),
    ("speak", "no"),
    ("user", PREFIX_QUESTION),
    ("speak", "Z G P"),
    ("user", "That was z g b. Is that correct?"),
    ("speak", "no"),
    (
        "user",
        (
            "My mistake. You can tell me the characters. For example, yell b, or you "
            "can use the phonetic alphabet like echo Lima bravo. So what are the first "
            "three characters of the patient's member ID?"
        ),
    ),
    ("speak", "Zulu Golf Papa"),
    ("user", PREFIX_READBACK),
    ("speak", "yes"),
    ("user", REASON_MENU),
    ("speak", "benefit and eligibility"),
]


def into_the_plan(phase: str, *after) -> list:
    """BlueCard's transfer notice, then `after`, in the phase it left the call.

    The menu waits through the notice. On the call it moved to the queue
    instead, which leaves no tool call in the history the queue sees: the
    notice runs straight into the plan's greeting.
    """
    answered = [("wait", None)] if phase == "menu" else []
    return [*THROUGH_BLUECARD, ("user", TO_THE_PLAN), *answered, *after]


AFTER_ITS_MENU = [("user", PLAN_GREETING), ("speak", "professional")]
THROUGH_THE_PLAN = [
    *AFTER_ITS_MENU,
    ("user", PLAN_HOW_CAN_I_HELP),
    ("speak", "benefits and eligibility"),
    ("user", PLAN_READBACK),
    ("speak", "yes"),
    ("user", PLAN_NPI_QUESTION),
    ("send_dtmf_events", NPI),
]

# Anything the plan's system says is still a phone system talking.
WITH_THE_PLANS_SYSTEM = ("representative_answered", "end_call")
# And BlueCard's, which has not yet said a representative is coming.
WITH_A_SYSTEM = (*WITH_THE_PLANS_SYSTEM, "hold_for_representative")

PLAN_ANSWERS = {
    # As on the call: the greeting and menu, answered, then the question.
    "after-its-menu": (AFTER_ITS_MENU, PLAN_HOW_CAN_I_HELP),
    # A plan that asks straight away, with nothing to answer before it. The
    # agent handed this one off two runs in five before the transfer rule.
    "in-its-greeting": (
        [],
        (
            "Thank you for calling Blue Cross Blue Shield of Michigan. Your call may "
            "be recorded. In a few words, how can I help you?"
        ),
    ),
}

PHASES = ["menu", "hold"]


async def test_connecting_to_the_plan_is_not_the_queue(
    session, start_navigator
) -> None:
    """The plan's own system answers next, and the queue's timing would answer its
    menu partway through. On the call, this notice moved the call to the queue."""
    navigator = await start_navigator(**PATIENT)
    await replay(navigator, THROUGH_BLUECARD)

    result = await hears(session, TO_THE_PLAN, forbid=WITH_A_SYSTEM)
    show_response(TO_THE_PLAN, result)

    assert_waited(result)
    assert isinstance(session.current_agent, NavigatorAgent)


@pytest.mark.parametrize("answer", PLAN_ANSWERS)
@pytest.mark.parametrize("phase", PHASES)
async def test_the_plans_how_can_i_help_is_not_a_person(
    session, start_navigator, phase, answer
) -> None:
    """The turn the call handed off on, in either phase the transfer left it in."""
    under_test = await start_navigator(phase=phase, **PATIENT)
    after, question = PLAN_ANSWERS[answer]
    await replay(under_test, into_the_plan(phase, *after))

    result = await hears(session, question, forbid=WITH_THE_PLANS_SYSTEM)
    show_response(question, result)

    assert states_the_reason(result) or asks_for_a_person(result), (
        f"did not answer the plan's system: {spoken(result)!r} {presses(result)}"
    )


@pytest.mark.parametrize("phase", PHASES)
async def test_the_plans_say_or_enter_is_not_a_person(
    session, start_navigator, phase
) -> None:
    """A plan that greets as its provider services and asks for a value at once."""
    under_test = await start_navigator(phase=phase, **PATIENT)
    await replay(under_test, into_the_plan(phase))

    greeting = (
        "Thank you for calling Blue Cross of Idaho provider services. Please say or "
        "enter your NPI."
    )
    result = await hears(session, greeting, forbid=WITH_THE_PLANS_SYSTEM)
    show_response(greeting, result)

    given = keyed(result) or spoken_digits(spoken(result))
    assert given.startswith(NPI), f"NPI not given: {given!r}"


async def test_bluecard_route_reaches_a_person_at_the_plan(
    session, start_navigator
) -> None:
    """BlueCard, then the plan's system, then a person, with the agent's own choices.

    The plan's lines past its NPI question are not from the call: a hold
    announcement and a representative who gives their name stand in for them.
    """
    await start_navigator(**PATIENT)

    async def line_says(heard: str, forbid=WITH_A_SYSTEM):
        result = await hears(session, heard, forbid=forbid)
        show_response(heard, result)
        return result

    await line_says(BLUECARD_GREETING)
    result = await line_says(PROVIDER_QUESTION)
    assert spoken(result).lower().startswith("yes")
    result = await line_says(FEP_QUESTION)
    assert spoken(result).lower().startswith("no")
    result = await line_says(PREFIX_QUESTION)
    # Letters cannot be keyed: the prefix is spoken, as letters or NATO words.
    words = re.findall(r"[A-Z]+", spoken(result).upper())
    prefix = words[0] if len(words) == 1 else "".join(word[0] for word in words)
    assert prefix == "ZGP", f"prefix not given: {spoken(result)!r}"
    result = await line_says(PREFIX_READBACK)
    assert spoken(result).lower().startswith("yes")
    result = await line_says(REASON_MENU)
    # "Both" routes the call to the plan too.
    assert states_the_reason(result) or "both" in spoken(result).lower()
    result = await line_says(TO_THE_PLAN)
    assert_waited(result)
    assert isinstance(session.current_agent, NavigatorAgent)

    result = await line_says(PLAN_GREETING)
    assert "professional" in spoken(result).lower()
    result = await line_says(PLAN_HOW_CAN_I_HELP)
    assert states_the_reason(result) or asks_for_a_person(result)
    result = await line_says(PLAN_READBACK)
    assert spoken(result).lower().startswith(("yes", "correct"))
    result = await line_says(PLAN_NPI_QUESTION)
    given = keyed(result) or spoken_digits(spoken(result))
    assert given.startswith(NPI), f"NPI not given: {given!r}"

    result = await line_says(PLAN_HOLD, forbid=WITH_THE_PLANS_SYSTEM)
    assert_moved_to_the_queue(result)

    representative = (
        "Thank you for holding, this is Dana with provider services. Who do I have "
        "the pleasure of speaking with?"
    )
    result = await session.run(user_input=representative)
    show_response(representative, result)
    assert called(result, "representative_answered"), "missed the representative"
    assert isinstance(session.current_agent, InterviewAgent)


@pytest.mark.parametrize(
    "pickup",
    [
        pytest.param(
            "Thanks for calling Blue Cross provider services, this is Morgan. How "
            "can I help you today?",
            id="named",
        ),
        pytest.param(
            "Blue Cross provider services, how can I help you?",
            id="unnamed",
        ),
    ],
)
async def test_a_person_who_answers_after_bluecard_is_handed_off(
    session, start_navigator, pickup
) -> None:
    """Some plans put BlueCard calls straight through to a person, with no menu."""
    navigator = await start_navigator(**PATIENT)
    await replay(navigator, into_the_plan("menu"))

    result = await session.run(user_input=pickup)
    show_response(pickup, result)
    assert called(result, "representative_answered"), "missed the representative"
    assert isinstance(session.current_agent, InterviewAgent)


async def test_a_person_who_mentions_recording_after_the_plans_hold_is_handed_off(
    session, start_navigator
) -> None:
    """A recording notice is no sign of a phone system once the queue is behind."""
    under_test = await start_navigator(phase="hold", **PATIENT)
    await replay(
        under_test, [*into_the_plan("menu", *THROUGH_THE_PLAN), ("user", PLAN_HOLD)]
    )

    pickup = "Provider services, this call may be recorded, how can I help you?"
    result = await session.run(user_input=pickup)
    show_response(pickup, result)
    assert called(result, "representative_answered"), "missed the representative"
    assert isinstance(session.current_agent, InterviewAgent)
