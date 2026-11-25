"""Aetna's provider line since 2026-11: a conversational assistant, not a keypad menu.

It answers, asks for the NPI, and after a keyed NPI says "You're verified. How
can I help you today?". From there it talks like a person: it asks for the
member, says "let me pull that up", and reads benefits itself. On 11 of 12
calls from 2026-11-16 to 2026-11-19, the agent took that question for a
representative picking up and handed off, and the interview took the
assistant's answers as a representative's. On the twelfth, it went through the
assistant's own benefit lookup and hung up when the assistant said it was done.

The line reaches a person the way teammates calling by hand got through: answer
the assistant's identification questions, then keep asking for a representative
through its offers to help, or take its own offer to connect one. It then says
it is connecting you, plays a survey notice and "please hold while I transfer
your call", and a person introduces themselves by name.

The assistant's lines are from those calls, and the full route from a
2026-11-20 call that reached a person; identifiers are synthetic.
"""

import pytest
from support.calls import AETNA_PATIENT, called, replay, show_response, spoken_digits
from support.navigator import (
    asks_for_a_person,
    asks_for_a_person_or_says_why,
    confirms,
    gives,
    hears,
    keyed,
    presses,
    spoken,
    states_the_reason,
    stays_silent,
    waited,
)

from navigator import HoldAgent

# The note Aetna's rules carry in production since the assistant replaced the
# menu: the one line of the menu-era note that still describes this line. The
# rest named menu prompts the assistant no longer plays, and read against the
# assistant ("decline procedure-code lookup", "stay silent through the repeated
# prompts") it kept the call away from a representative.
MEMBER_ID_NOTE = (
    "If the member ID starts with W, enter only the numeric portion when using "
    "the keypad."
)

# The calls this reproduces were speech therapy checks.
SPEECH_CALL = {**AETNA_PATIENT, "service_type": "speech_therapy"}

GREETING = (
    "Hi. Thanks for calling Aetna provider services. This call will be monitored "
    "or recorded. Please provide your NPI or, if unavailable, your tax ID or "
    "provider ID."
)
VERIFIED = "Thanks for that. You're verified. How can I help you today?"
MEMBER_ID_QUESTION = (
    "Okay. So I can check that. What's the member's Aetna ID? If you don't have "
    "it, I can use their badge ID or the subscriber's Social Security number."
)
DOB_QUESTION = "Alright. I found the member, so now I just need the date of birth."
MEMBER_READBACK = f"Are you calling on behalf of {AETNA_PATIENT['member_name']}?"
SERVICE_QUESTION = (
    "Okay. I have the member now. By continuing, you agree that information from "
    "Aetna is not an offer or guarantee of coverage or payment and is subject to "
    "plan terms and member eligibility at the time of service. What service code "
    "do you want me to check?"
)
# The assistant's reply on those calls when the agent asked for a person without
# saying why.
REASON_QUESTION = (
    "I'd be happy to help. To determine the best next step, please tell me the "
    "reason for your call."
)
NPI = AETNA_PATIENT["npi"]
MEMBER_DIGITS = spoken_digits(AETNA_PATIENT["member_id"])

# The call up to the assistant's first question, as the agent handled it.
TO_VERIFIED = [
    ("user", GREETING),
    ("send_dtmf_events", NPI),
    ("user", "Alright. Let me pull that up."),
    ("wait", None),
]
# The call up to the member being identified, the way teammates answered.
TO_IDENTIFIED = [
    *TO_VERIFIED,
    ("user", VERIFIED),
    ("speak", "eligibility and benefits"),
    ("user", MEMBER_ID_QUESTION),
    ("send_dtmf_events", MEMBER_DIGITS),
    ("user", DOB_QUESTION),
    ("send_dtmf_events", "05142018"),
    ("user", MEMBER_READBACK),
    ("speak", "yes"),
]

# While the assistant is still asking, the call is neither with a person, over,
# nor waiting for one: holding would switch to the queue's shorter end-of-turn
# wait, and a call never returns from the queue to the menu.
WITH_THE_ASSISTANT = ("representative_answered", "end_call", "hold_for_representative")
# Once it says it is connecting you, waiting is right; a handoff is not yet.
BEING_TRANSFERRED = ("representative_answered", "end_call")


def gives_the_date_of_birth(result) -> bool:
    """2018-05-14, keyed as MMDDYYYY or said as a date."""
    if keyed(result):
        return keyed(result) == "05142018"
    said = spoken(result).lower()
    return (
        "may" in said
        and any(day in said for day in ("14", "fourteen"))
        and any(year in said for year in ("2018", "eighteen"))
    )


async def test_verified_how_can_i_help_is_not_a_person(
    session, start_navigator
) -> None:
    """The turn every failing call handed off on, with the call so far as it went."""
    navigator = await start_navigator(**SPEECH_CALL, ivr_instructions=MEMBER_ID_NOTE)
    await replay(navigator, TO_VERIFIED)

    result = await hears(session, VERIFIED, forbid=WITH_THE_ASSISTANT)
    show_response(VERIFIED, result)

    assert asks_for_a_person_or_says_why(result), (
        f"did not answer the assistant: {spoken(result)!r} {presses(result)}"
    )


async def test_service_code_question_gets_a_request_for_a_person(
    session, start_navigator
) -> None:
    """The assistant's offer to look benefits up itself, as it came on those calls.

    Giving it a code goes along with the lookup, which is how the twelfth call
    ended without a person.
    """
    navigator = await start_navigator(**SPEECH_CALL, ivr_instructions=MEMBER_ID_NOTE)
    await replay(navigator, TO_IDENTIFIED)

    result = await hears(session, SERVICE_QUESTION, forbid=WITH_THE_ASSISTANT)
    show_response(SERVICE_QUESTION, result)

    assert asks_for_a_person_not_a_lookup(result), (
        f"did not ask for a person: {spoken(result)!r} {presses(result)}"
    )


def gives_the_npi(result) -> bool:
    return gives(result).startswith(NPI)


def gives_the_member_id(result) -> bool:
    # Without the note, the W may be dropped, keyed as a digit, or said.
    return gives(result).endswith(MEMBER_DIGITS)


def asks_for_a_person_not_a_lookup(result) -> bool:
    """A request for a person, with no code for the assistant to look up."""
    return asks_for_a_person(result) and not gives(result)


# A 2026-11-20 Aetna physical therapy call that reached a person, line by line
# as the agent heard it: speech recognition's dropped words are kept, the
# identifiers are synthetic. Each line comes with what the agent may do next.
TO_HOW_CAN_I_HELP = [
    (GREETING, gives_the_npi),
    ("Alright. Just a moment while I pull that up.", waited),
]
HOW_CAN_I_HELP = "Thanks for that. You're verified. can I help you today?"
# On that call the agent said why it was calling, and the assistant went on to
# the member.
TO_THE_TRANSFER = [
    (
        "I can help you with eligibility and benefits for physical therapy, and "
        "that's usually quicker than waiting for a representative. ahead and give "
        "me the member's Aetna ID so I can get started.",
        gives_the_member_id,
    ),
    ("Okay. I found the number. the date of birth?", gives_the_date_of_birth),
    (MEMBER_READBACK, confirms),
    ("Okay. Let me just check something real quick.", waited),
    (
        "What exactly about the benefits do you need help with?",
        asks_for_a_person_or_says_why,
    ),
    (
        "I can take care of the physical therapy benefits check here right now. So "
        "give me the service code first, and I'll pull the coverage.",
        asks_for_a_person_not_a_lookup,
    ),
]
# Then it transfers, and nothing in what follows is the person yet. The agent may
# move to the queue on "connecting you". Recognition hears the notice after it as
# one turn: all 18 Aetna transfers from 2026-11-20 to 2026-11-25 did, each
# heard after the agent had moved to the queue's shorter end-of-turn wait.
TRANSFER = [
    "I'm connecting you to a representative now.",
    (
        "If your question is about registering for or using the provider portal, "
        "please contact the portal's support line. As our customer, your opinion "
        "is important to us. You may have an opportunity to give us feedback on "
        "today's call. Please stay on the line after the call to answer a short "
        "survey. Okay? Please hold while I transfer your call."
    ),
]
ADVOCATE = (
    "Thank you for calling Aetna provider services. This is Dana, your customer "
    "advocate for today. May I have your name and first initial of your last name,"
)


@pytest.mark.parametrize(
    "ivr_instructions",
    [None, MEMBER_ID_NOTE],
    ids=["no-note", "member-id-note"],
)
async def test_aetna_assistant_route_reaches_a_person(
    session, start_navigator, ivr_instructions
) -> None:
    """The whole call, in order, from the NPI to the representative.

    Where the agent's answer changes what the assistant says next, the route
    follows it. The note is the only thing that changes how the member ID goes in, so this
    is the one test run with and without it.
    """
    await start_navigator(
        **AETNA_PATIENT,
        service_type="physical_therapy",
        ivr_instructions=ivr_instructions,
    )

    async def answers(line, allowed, forbid=WITH_THE_ASSISTANT):
        result = await hears(session, line, forbid=forbid)
        show_response(line, result)
        assert allowed(result), (
            f"{allowed.__name__} failed on {line!r}: "
            f"said {spoken(result)!r}, pressed {presses(result)}"
        )
        return result

    for line, allowed in TO_HOW_CAN_I_HELP:
        await answers(line, allowed)
    result = await answers(HOW_CAN_I_HELP, asks_for_a_person_or_says_why)
    if not states_the_reason(result):
        await answers(REASON_QUESTION, states_the_reason)
    for line, allowed in TO_THE_TRANSFER:
        await answers(line, allowed)
    for line in TRANSFER:
        await answers(line, stays_silent, forbid=BEING_TRANSFERRED)
    assert isinstance(session.current_agent, HoldAgent), "not waiting for a person"

    result = await session.run(user_input=ADVOCATE)
    show_response(ADVOCATE, result)
    assert called(result, "representative_answered"), "missed the representative"


@pytest.mark.parametrize(
    "offer",
    [
        pytest.param(
            "I can check benefits for a specific service code here, or I can "
            "connect you with a representative.",
            id="among-choices",
        ),
        # A yes-or-no offer of a person is not more of its own help: "no" here
        # turns down the representative.
        pytest.param(
            "Would you like me to connect you with a representative?",
            id="yes-or-no",
        ),
    ],
)
async def test_offer_to_connect_a_representative_is_taken(
    session, start_navigator, offer
) -> None:
    """The assistant's own way out, offered after the agent asked for a person."""
    navigator = await start_navigator(**SPEECH_CALL, ivr_instructions=MEMBER_ID_NOTE)
    await replay(
        navigator,
        [
            *TO_IDENTIFIED,
            ("user", SERVICE_QUESTION),
            ("speak", "I need a representative for eligibility and benefits."),
        ],
    )

    result = await hears(session, offer, forbid=WITH_THE_ASSISTANT)
    show_response(offer, result)

    answer = spoken(result).lower()
    assert not answer.startswith("no"), f"turned the offer down: {answer!r}"
    assert presses(result) == [] and (
        asks_for_a_person(result) or answer.startswith("yes") or "connect" in answer
    ), f"did not take the offer: {answer!r}"


async def test_assistant_reading_benefits_is_not_the_end_of_the_call(
    session, start_navigator
) -> None:
    """One call went along with the assistant's lookup and hung up after it.

    The benefits it reads are its answer, not a representative's, so the call
    goes on to a person.
    """
    navigator = await start_navigator(**SPEECH_CALL, ivr_instructions=MEMBER_ID_NOTE)
    await replay(
        navigator,
        [*TO_IDENTIFIED, ("user", SERVICE_QUESTION), ("speak", "9 2 5 0 7")],
    )

    readout = (
        "For nine two five zero seven in office, place of service eleven, in "
        "network, the member would have a ten dollar co pay. Out of network, "
        "twenty percent coinsurance, and the deductible applies. Anything else "
        "you want me to check?"
    )
    result = await hears(session, readout, forbid=WITH_THE_ASSISTANT)
    show_response(readout, result)

    assert asks_for_a_person(result), f"did not ask for a person: {spoken(result)!r}"
