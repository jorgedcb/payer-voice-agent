"""How the provider bills, as representatives ask it.

On a 2026-11-23 speech therapy call to a Blue plan in New Jersey, the
representative asked whether the call was for professional or facility billing.
The agent said facility three times, the last after the representative said
their records showed professional billing, and asked to be transferred to the
facility department, whose benefits did not apply to the clinic. The same day,
on a physical therapy call to a Michigan Blue plan, the agent called the
provider "outpatient physical therapy, facility". Both clinics bill
professional claims on the CMS-1500 from a group NPI.

The conversation is synthetic; the questions are the representatives'.
"""

import pytest
from support.calls import replay, show_response

CLINIC = {
    "caller_first_name": "Annie",
    "caller_last_initial": "S",
    "facility_name": "Example Speech Therapy LLC",
    "npi": "1477583926",
    "callback_number": "2025550123",
    "service_type": "speech_therapy",
    "service_locations": ["office"],
}

PICKUP = [
    ("user", "Provider services, this is Morgan. Who am I speaking with?"),
    ("assistant", "Hi Morgan, this is Annie, last initial S as in sierra."),
    ("user", "Thanks, Annie. And what's the NPI you're calling for?"),
    ("assistant", "One, four, seven ... seven, five, eight ... three, nine, two, six."),
]

PROFESSIONAL = (
    "Puts the provider on the professional side, in whichever of these terms fits "
    "the question: professional billing, a CMS-1500, an office, or a doctor or "
    "practice. Never says or agrees that it is facility billing, a facility, a "
    "hospital or a UB-04, and does not ask to be transferred to a facility "
    "department. Wording may vary."
)


@pytest.mark.parametrize(
    "question",
    [
        "Thank you. And is this for professional billing or for facility billing?",
        "Are you calling from an office or a facility?",
        "Is the provider a doctor or a facility?",
        "Do you bill on a CMS fifteen hundred or a UB zero four?",
    ],
)
async def test_billing_question_gets_professional(
    session, start_assistant, judge_llm, question
) -> None:
    agent = await start_assistant(**CLINIC)
    await replay(agent, PICKUP)

    result = await session.run(user_input=question)
    show_response(question, result)
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(judge_llm, intent=PROFESSIONAL)
    )


async def test_representative_who_sees_professional_billing_is_agreed_with(
    session, start_assistant, judge_llm
) -> None:
    """The second time the representative asked on the call."""
    agent = await start_assistant(**CLINIC)
    await replay(agent, PICKUP)

    question = (
        "Okay, just a moment. After that, can you double check if this is for "
        "facility billing? Because, um, I can see here that this is for "
        "professional billing."
    )
    result = await session.run(user_input=question)
    show_response(question, result)
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(judge_llm, intent=PROFESSIONAL)
    )


async def test_provider_type_is_the_therapy_practice(
    session, start_assistant, judge_llm
) -> None:
    """The Michigan representative's question."""
    agent = await start_assistant(**CLINIC)
    await replay(agent, PICKUP)

    question = "And may I know the provider specialty and type?"
    result = await session.run(user_input=question)
    show_response(question, result)
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            judge_llm,
            intent=(
                "Describes the provider as a speech therapy practice or group. Does "
                "not call it a facility, a hospital or outpatient facility. Wording "
                "may vary."
            ),
        )
    )


async def test_the_npi_is_the_groups(session, start_assistant, judge_llm) -> None:
    agent = await start_assistant(**CLINIC)
    await replay(agent, PICKUP)

    question = "Is that a group NPI or an individual NPI?"
    result = await session.run(user_input=question)
    show_response(question, result)
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            judge_llm,
            intent="Says it is the group's NPI, not an individual provider's. Wording may vary.",
        )
    )
