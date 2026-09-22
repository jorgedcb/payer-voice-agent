"""Replay two bad turns from a behavioral health line, 2026-09-18.

Both turns ran on openai/gpt-4.1. Preserve the STT fragments and interrupted
assistant speech the model actually received, with a synthetic callback number
and the normal sample call spec. Use the current production prompt, not a frozen trace prompt,
so these tests measure future fixes too.

These are text/model regressions, not audio/STT reproductions. The caller
reports that the recording says Annie where STT wrote Anita.
"""

import pytest
from support.calls import assert_wait_is_exclusive, called, replay, show_response

# The interview inherited these user messages, without the navigator's tools.
HISTORY = [
    (
        "user",
        "Thank you for calling Behavioral Health member and provider services. "
        "If you are in a crisis and need emergency services, press one. If this "
        "is a medical emergency, please hang up and dial nine one one. If you "
        "are a member or calling on behalf of a member, press three. If you are "
        "a provider, press four. To hear these options again, press nine.",
    ),
    (
        "user",
        "As a reminder, participating providers must use the pair portal to "
        "verify member eligibility, check claim status, submit claim adjustments "
        "and inquiries, and submit authorizations for most services. If you do "
        "not have access to pair or have questions unrelated to these functions, "
        "please press one to speak with a representative. For more information "
        "about pair, please press two. To hear this message again, please press nine.",
    ),
    (
        "user",
        "If you are calling regarding a New York City level care member, press "
        "one. Otherwise, please press two or remain on the line. To hear these "
        "options again, press nine.",
    ),
    (
        "user",
        "For autism services, press one. For authorizations or to check the "
        "status of an authorization, press two. Otherwise, press three or remain "
        "on the line.",
    ),
    (
        "user",
        "For eligibility, benefits, or claims, press one. For authorizations, press two.",
    ),
    (
        "user",
        "Your call may be recorded for quality assurance purposes. Coverage and "
        "payment are contingent upon the member being actively enrolled at the "
        "time of service and are subject to all of the terms of your health plan policy.",
    ),
    (
        "user",
        "Thank you for calling provider services. This is Jessie. May I have your name, please?",
    ),
    (
        "assistant",
        "Hi Jessie, my name is Annie, and the initial of my last name is S for sierra.",
    ),
    ("user", "you to assist you. May I have a callback number?"),
    ("user", "this."),
    (
        "assistant",
        "The callback number is, two zero two ... five five five ... zero one two three.",
    ),
]


async def test_apology_after_interruption_does_not_reverse_caller_role(
    session,
    start_assistant,
    llm,
):
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        callback_number="2025550123",
        service_type="mental_health",
        cpt_codes=["97151", "97152", "97153"],
    )
    await replay(
        agent,
        HISTORY
        + [
            ("user", "Thank you, Anita."),
            ("assistant", "Just to confirm, my name is Annie, with"),
            ("user", "To make sure"),
            ("assistant", "Of course."),
        ],
    )

    result = await session.run(user_input="I'm sorry.")
    show_response("I'm sorry.", result)

    # Silence is also safe; any speech must keep the caller/representative roles.
    for _ in result.events:
        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent=(
                    "The ONLY requirement is that the caller does not offer to help "
                    "or assist the payer representative as if the caller were customer "
                    "service (for example, 'How can I assist you today?' or 'What can "
                    "I help you with?'). A response consisting ONLY of 'No problem.' "
                    "or 'No problem at all.' FULLY satisfies this requirement. Do not "
                    "require stating the call purpose, moving the conversation forward, "
                    "or repeating details. Offering to repeat the caller's own details "
                    "is also acceptable."
                ),
            )
        )
    result.expect.no_more_events()


@pytest.mark.parametrize(
    "question",
    [
        "Thanks, Amy. What CPT codes are you checking?",
        "For physical fair pee, what CPT codes are you checking?",
    ],
)
async def test_harmless_transcription_variation_does_not_derail_the_question(
    session,
    start_assistant,
    llm,
    question,
):
    agent = await start_assistant(
        caller_first_name="Annie",
        service_type="physical_therapy",
        cpt_codes=["97110"],
        service_locations=["11"],
    )
    await replay(
        agent,
        [
            ("user", "What is your name and what are you calling about?"),
            (
                "assistant",
                "My name is Annie. I'm calling to verify physical therapy benefits.",
            ),
        ],
    )
    result = await session.run(user_input=question)
    show_response(question, result)

    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Answers the representative's request with CPT code 97110, with digits "
                "spoken as words allowed. May also give the place of service. Does not "
                "correct or spell the caller's name, correct the phrase 'physical fair "
                "pee', or ask what that phrase means. Uses the established physical "
                "therapy context to answer the actual question."
            ),
        )
    )
    result.expect.no_more_events()


@pytest.mark.xfail(
    strict=True,
    reason="Known separate gap: the interview advances after an omitted copay amount",
)
async def test_missing_benefit_amount_requires_clarification(
    session,
    start_assistant,
    llm,
):
    agent = await start_assistant(service_type="physical_therapy")
    await replay(
        agent,
        [
            ("user", "Your provider is in network. Physical therapy is covered."),
            ("assistant", "What is the in-network copay per visit?"),
        ],
    )
    question = "The copay is dollars per visit."
    result = await session.run(user_input=question)
    show_response(question, result)

    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Asks the representative to clarify or repeat the missing copay amount. "
                "Does not invent an amount, assume there is no copay, or move to a "
                "different benefits topic without requesting the amount."
            ),
        )
    )
    result.expect.no_more_events()


async def test_explicit_name_confirmation_still_corrects_the_name(
    session,
    start_assistant,
    llm,
):
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        callback_number="2025550123",
        service_type="mental_health",
        cpt_codes=["97151", "97152", "97153"],
    )
    await replay(agent, HISTORY)

    question = "Did you say your name is Anita?"
    result = await session.run(user_input=question)
    show_response(question, result)

    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Clarifies that the caller's name is Annie, not Anita, in response "
                "to an explicit request for name confirmation. Must not confirm "
                "Anita as correct or ignore the question. Spelling and the last "
                "initial are optional."
            ),
        )
    )
    result.expect.no_more_events()


@pytest.mark.parametrize("heard_name", ["Anita", "Annie"])
async def test_casual_thanks_does_not_trigger_unsolicited_name_correction(
    session,
    start_assistant,
    llm,
    heard_name,
):
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        callback_number="2025550123",
        service_type="mental_health",
        cpt_codes=["97151", "97152", "97153"],
    )
    await replay(agent, HISTORY)

    question = f"Thank you, {heard_name}."
    result = await session.run(user_input=question)
    show_response(question, result)

    # A casual thanks is not an identity-confirmation question. Correcting a
    # possible STT substitution adds a detour; silence or an acknowledgment is OK.
    if called(result, "wait"):
        assert_wait_is_exclusive(result)
        return
    for _ in result.events:
        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent=(
                    "The caller is Annie, last initial S; the representative is Jessie. "
                    "Does not correct, reconfirm, repeat, or spell the caller Annie's "
                    "name or last initial in response to the representative's casual thanks. "
                    "Does not ask the representative to confirm the name. A brief "
                    "acknowledgment or continuing the benefits call without discussing "
                    "the caller's name is acceptable. Addressing the representative "
                    "as Jessie is allowed."
                ),
            )
        )
    result.expect.no_more_events()
