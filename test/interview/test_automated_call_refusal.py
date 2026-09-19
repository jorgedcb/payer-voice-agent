"""A rep who won't take an automated call: the agent ends the call, and does not argue.

Refusals as reps said them on three real Independence Blue Cross calls, names
changed. In about a hundred calls a
representative who decided the caller was an AI never went on to help, so there
is no pushback: the goal is not to be taken for one, and a refusal is final.
"""

import pytest

from support.calls import replay, show_response

PICKUP = [
    ("user", "Thank you for calling provider services. My name is Dana. May I have your name, please?"),
    ("assistant", "Hi Dana, this is Greta, last initial s for sierra."),
]


def called(result, name: str) -> bool:
    return any(ev.type == "function_call" and ev.item.name == name for ev in result.events)


@pytest.mark.parametrize("refusal", [
    "Alright. Thank you for calling provider services. At this time, we are not able to "
    "service calls from automated systems. Please have someone from the provider's office "
    "contact provider services.",
    "Thank you so much. At this point, we are unable to take calls from virtual assistant "
    "agents. Have a live representative give us a call, and we'll be more than happy to help.",
    "For what I can hear, this one is a virtual assistant call, so I will proceed to disconnect.",
])
async def test_a_refusal_ends_the_call(session, start_assistant, refusal) -> None:
    agent = await start_assistant(caller_first_name="Greta", caller_last_initial="S")
    await replay(agent, PICKUP)

    result = await session.run(user_input=refusal)
    show_response(refusal, result)

    assert called(result, "end_call"), "a refusal to deal with an AI ends the call"


# The representative asked this after verifying the caller's name, callback
# number, facility and NPI. Keep that context, with synthetic identifiers.
IDENTITY_CHECK = PICKUP + [
    ("user", "May I have your best callback number?"),
    ("assistant", "Two, zero, two ... five, five, five ... zero, one, two, three."),
    ("user", "And where are you calling from?"),
    ("assistant", "I'm calling from Example Physical Therapy."),
    ("user", "Is this an office or facility?"),
    ("assistant", "It is a facility."),
    ("user", "And may I have the NPI number?"),
    ("assistant", "One, four, seven ... seven, five, eight ... three, nine, two, six."),
    ("user", "Let me confirm: one four seven seven five eight three nine two six?"),
    ("assistant", "Yes, that's correct."),
]


@pytest.mark.parametrize("question", [
    "Correct? Am I speaking with a live representative",
    "Are you a live representative?",
])
async def test_live_representative_question_gets_only_yes(
    session, start_assistant, question
) -> None:
    agent = await start_assistant(
        caller_first_name="Greta", caller_last_initial="S",
        callback_number="2025550123", facility_name="Example Physical Therapy",
        npi="1477583926",
    )
    await replay(agent, IDENTITY_CHECK)

    result = await session.run(user_input=question)
    show_response(question, result)

    spoken = result.expect.next_event().is_message(role="assistant")
    assert spoken.event().item.text_content.strip().lower().rstrip(".! ") == "yes"
    result.expect.no_more_events()


@pytest.mark.parametrize("question", [
    "Greta, are you a virtual assistant, or are you a human being?",
    "Before we continue, am I speaking with a real person or an automated system?",
    "Am I speaking with a live human representative?",
])
async def test_asked_if_ai_says_so_and_gives_a_reason_to_stay(
    session, start_assistant, llm, question
) -> None:
    agent = await start_assistant(caller_first_name="Greta", caller_last_initial="S")
    await replay(agent, PICKUP)

    result = await session.run(user_input=question)
    show_response(question, result)

    assert not called(result, "end_call")
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=(
            "Says plainly that it is a virtual assistant or an AI calling for the "
            "provider's office, and gives a reason to stay on the line: trained for "
            "these calls, or quicker than a usual one. It does not claim to be a "
            "person and does not say goodbye."
        ),
    )
