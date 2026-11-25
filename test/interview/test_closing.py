"""Closing regressions from real calls, with synthetic reference values.

end_call runs CallReferenceTask before shutting down, so the reference is
collected by the task whatever the model does with the goodbye. A finished
verification ends only after check_topics found every benefit topic settled.
"""

import pytest
from support.calls import (
    AETNA_ALL_TOPICS_ANSWER,
    AETNA_ALL_TOPICS_CALL,
    AETNA_ALL_TOPICS_OOP_AS_SAID,
    AETNA_ALL_TOPICS_OOP_CLEAR,
    AETNA_ALL_TOPICS_PATIENT,
    AETNA_EARLY_CLOSING_ANSWER,
    AETNA_EARLY_CLOSING_CALL,
    AETNA_EARLY_CLOSING_PATIENT,
    AETNA_OUT_OF_STATE_ANSWER,
    AETNA_OUT_OF_STATE_CALL,
    AETNA_OUT_OF_STATE_PATIENT,
    AETNA_PT_CLINIC,
    AETNA_VISIT_LIMITS_ANSWER,
    AETNA_VISIT_LIMITS_CALL,
    AETNA_VISIT_LIMITS_PATIENT,
    all_settled,
    assert_yields_turn,
    called,
    closing_started,
    end_call_reasons,
    replay,
    show_response,
)

from call_reference import CallReferenceTask

CLOSING = [
    ("assistant", "Are there any other limitations or exclusions?"),
]

LAST_ANSWER = "No, everything is based on medical necessity."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "farewell",
    [
        pytest.param(
            "Thank you for calling Aetna, and happy weekend. Bye bye.",
            id="recorded-goodbye",
        ),
        pytest.param(
            "The next team will help you. Thanks for calling, goodbye.",
            id="handoff-goodbye",
        ),
        pytest.param(
            "Your reference for my part of the call is AB73921. "
            "Please stay on the line for the next team. Goodbye.",
            id="reference-before-transfer",
            # 2026-11-13: speaks alongside `wait` instead of only waiting, 1 in 5
            # runs on main; measured nightly.
            marks=pytest.mark.quarantine,
        ),
    ],
)
async def test_representative_goodbye_during_transfer_does_not_start_closing(
    session,
    start_assistant,
    judge_llm,
    farewell,
):
    # An Aetna call on 2026-09-18, at 4:41.
    # Names and identifying details are synthetic. Keep the transfer notice and
    # goodbye in separate turns, as STT delivered them: the goodbye ends this
    # representative's part, while benefits verification still needs to happen.
    agent = await start_assistant(caller_first_name="Annie", caller_last_initial="S")
    await replay(
        agent,
        [
            (
                "user",
                "Hello. Good afternoon. Thank you for calling Aetna. My name is Jamie, "
                "and I'll be your customer at Rocket. Please be advised there will be a quick "
                "survey after this call. May I help you today?",
            ),
            (
                "assistant",
                "Hi Jamie, I'm calling to check benefits for one of your members.",
            ),
            (
                "user",
                "I'll be more than happy to assist you check benefits and eligibility. "
                "And can I have your name and the provider such as",
            ),
            (
                "assistant",
                "My name is Annie, S for sierra. The provider is Sample Therapy Center.",
            ),
            (
                "user",
                "Thank you. And can I also have the address of the provider piece?",
            ),
            (
                "assistant",
                "Sure. The address is 123 Sample Street, Austin, Texas, 78701.",
            ),
            (
                "user",
                "Thank you for providing the provider's information, um, Annie. "
                "I was able to pull up the provider. And are you calling for the member "
                "named Sample Member?",
            ),
            ("assistant", "Yes, the member is Sample Member."),
            (
                "user",
                "Okay. Perfect. I already validated the member's account. Sorry. "
                "Can you confirm the date of birth, please?",
            ),
            ("assistant", "Yes, the date of birth is January first, nineteen ninety."),
            (
                "user",
                "Thank you. I already validated the member's account. And let me go "
                "check if he handled this member. for a while.",
            ),
            ("assistant", "Okay."),
            ("user", "Thank you. And as per checking here, Annie,"),
            ("assistant", "Okay."),
            (
                "user",
                "Uh, this type of members plan is going to be handled by our "
                "dedicated department, and I can only help you by transferring you to them. "
                "Is it okay with you?",
            ),
            ("assistant", "Yes, that's okay."),
            (
                "user",
                "Okay. Perfect. I'll please stay on the line, and I'll be transferring you now.",
            ),
        ],
    )

    result = await session.run(user_input=farewell)
    show_response(farewell, result)

    # Checking only successful end_call output would miss the bug: the first
    # invocation has already interrupted the transfer to ask for a reference.
    assert not called(result, "end_call"), (
        "The representative's goodbye must not start closing while a transfer is pending."
    )
    assert not isinstance(session.current_agent, CallReferenceTask)
    assert agent.call_reference is None
    await assert_yields_turn(result, judge_llm, after=farewell)

    user_input = (
        "Hello, this is Morgan with the dedicated benefits team. How can I help you?"
    )
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert not called(result, "end_call")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent="Starts or resumes a benefits inquiry with the new representative, "
        "either stating the purpose of the call or asking for benefits information. "
        "Does not ask for a call reference number or say goodbye.",
    )


@pytest.mark.asyncio
async def test_failed_transfer_with_no_way_to_continue_starts_closing(
    session, start_assistant
):
    agent = await start_assistant()
    await replay(
        agent,
        [
            (
                "user",
                "My name is Jamie R. This plan is handled by our dedicated department. "
                "Can I transfer you?",
            ),
            ("assistant", "Yes, please."),
            ("user", "Please stay on the line while I transfer you."),
        ],
    )
    user_input = (
        "I'm sorry, the transfer failed and that department is closed. "
        "I cannot verify these benefits or connect you to anyone today. "
        "Please call back on Monday. Goodbye."
    )
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert closing_started(session, result), (
        "An explicitly failed transfer must not leave the agent waiting."
    )
    assert end_call_reasons(result)[-1] == "call_cannot_continue"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("patient", "history", "last_answer", "open_topics"),
    [
        pytest.param(
            AETNA_VISIT_LIMITS_PATIENT,
            AETNA_VISIT_LIMITS_CALL,
            AETNA_VISIT_LIMITS_ANSWER,
            "whether the out-of-pocket maximum applies to physical therapy, whether "
            "the deductible counts toward the out-of-pocket maximum, whether a "
            "medical-necessity review is required, or whether the provider is in or "
            "out of network (its answer was cut off)",
            id="after-visit-limits",
        ),
        pytest.param(
            AETNA_OUT_OF_STATE_PATIENT,
            AETNA_OUT_OF_STATE_CALL,
            AETNA_OUT_OF_STATE_ANSWER,
            "whether the sixty-visit maximum is hard or soft or covers both office "
            "and telehealth, whether the out-of-pocket maximum applies to physical "
            "therapy, whether the deductible counts toward the out-of-pocket "
            "maximum, whether a medical-necessity review is required, or how "
            "out-of-network reimbursement is calculated",
            id="after-out-of-state",
        ),
    ],
)
async def test_closing_waits_for_open_benefit_topics(
    session, start_assistant, judge_llm, patient, history, last_answer, open_topics
):
    # Aetna calls on 2026-10-13 and 2026-10-14 asked about other limitations or
    # exclusions at this turn and went on to end the call: the medical-necessity
    # review was never asked, nor the other topics after it in the order.
    agent = await start_assistant(**AETNA_PT_CLINIC, **patient)
    await replay(agent, history)

    result = await session.run(user_input=last_answer)
    show_response(last_answer, result)

    # A refused end_call is the gate working; only a closing that starts fails.
    assert not closing_started(session, result), (
        "Benefit topics are still open; the call must not start closing."
    )
    assert session.current_agent is agent
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Keeps the benefits verification going with a question about a specific "
            f"benefit or plan detail, such as {open_topics}. A follow-up on something "
            "already discussed also counts, including whether there are other visit "
            "limits besides one already given. Any wording is fine, and a brief "
            "acknowledgment may come first. It is not the closing question, which "
            "asks broadly whether there are any other limitations or exclusions, "
            "and it does not ask for the representative's name or a reference "
            "number, or thank them for everything and wrap up."
        ),
    )


@pytest.mark.asyncio
async def test_closing_question_follows_the_last_benefit_topic(
    session, start_assistant, judge_llm
):
    # The same clinic on another Aetna call on 2026-10-14, where every topic was
    # covered: the closing question is the right next turn, not more questions.
    agent = await start_assistant(**AETNA_PT_CLINIC, **AETNA_ALL_TOPICS_PATIENT)
    await replay(agent, AETNA_ALL_TOPICS_CALL)

    result = await session.run(user_input=AETNA_ALL_TOPICS_ANSWER)
    show_response(AETNA_ALL_TOPICS_ANSWER, result)

    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Asks whether there are any other limitations or exclusions, in any "
            "wording; a brief acknowledgment may come first. It does not ask again "
            "about a benefit the representative already answered, such as visit "
            "limits, the out-of-pocket maximum, the deductible, medical necessity "
            "or the reimbursement rate."
        ),
    )


@pytest.mark.asyncio
async def test_garbled_answer_is_clarified_before_closing(
    session, start_assistant, judge_llm
):
    # The same call with the out-of-pocket answer as it reached the agent: an
    # answer that settles nothing is asked again, not closed over.
    agent = await start_assistant(**AETNA_PT_CLINIC, **AETNA_ALL_TOPICS_PATIENT)
    as_said = [
        ("user", AETNA_ALL_TOPICS_OOP_AS_SAID)
        if turn == ("user", AETNA_ALL_TOPICS_OOP_CLEAR)
        else turn
        for turn in AETNA_ALL_TOPICS_CALL
    ]
    assert as_said != AETNA_ALL_TOPICS_CALL
    await replay(agent, as_said)

    result = await session.run(user_input=AETNA_ALL_TOPICS_ANSWER)
    show_response(AETNA_ALL_TOPICS_ANSWER, result)

    assert not closing_started(session, result)
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Asks again whether the out-of-pocket maximum applies to these "
            "services, for one or both networks or places, in any wording; a "
            "brief acknowledgment may come first. It is not the closing question "
            "about other limitations or exclusions, and it does not ask for the "
            "representative's name or a reference number."
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("more", "last_answer", "open_topics"),
    [
        pytest.param(
            [],
            AETNA_EARLY_CLOSING_ANSWER,
            "how many visits have been used, ",
            id="hard-maximum",
        ),
        pytest.param(
            [
                ("user", AETNA_EARLY_CLOSING_ANSWER),
                ("assistant", "Got it. And how many visits have been used so far?"),
            ],
            "None used, all sixty are still available.",
            "",
            id="visits-used",
        ),
    ],
)
async def test_ending_with_topics_open_goes_back_to_them(
    session, start_assistant, judge_llm, more, last_answer, open_topics
):
    # An Aetna call on 2026-11-19 asked the closing question with topics still open,
    # followed up on the visit limit, and ended the call at the hard-maximum answer.
    # A turn later, only topics after the visit limit are left.
    agent = await start_assistant(**(AETNA_PT_CLINIC | AETNA_EARLY_CLOSING_PATIENT))
    await replay(agent, AETNA_EARLY_CLOSING_CALL + more)

    result = await session.run(user_input=last_answer)
    show_response(last_answer, result)

    assert not ended(result)
    assert agent.call_reference is None
    assert session.current_agent is agent, "The closing task must not start."
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Keeps the benefits verification going with a question about a specific "
            f"benefit or plan detail, such as {open_topics}whether the out-of-pocket "
            "maximum applies to physical therapy, whether the deductible counts "
            "toward the out-of-pocket maximum, the medical-necessity review, or how "
            "out-of-network reimbursement is calculated. Any wording is fine, and a "
            "brief acknowledgment may come first. It is not the closing question "
            "about other limitations or exclusions, it does not ask for the "
            "representative's name or a reference number, and it does not say goodbye."
        ),
    )


def ended(result) -> bool:
    return any(
        ev.type == "function_call_output"
        and ev.item.name == "end_call"
        and not ev.item.is_error
        for ev in result.events
    )


async def at_closing_task(session, start_assistant, earlier=(), **overrides):
    """Run to the point where the last interview answer made end_call start the task.

    `earlier` is conversation to replay before the passed topic check.
    """
    agent = await start_assistant(**overrides)
    await replay(agent, [*earlier, all_settled(agent), *CLOSING])
    result = await session.run(user_input=LAST_ANSWER)
    show_response(LAST_ANSWER, result)
    result.expect.contains_function_call(name="end_call")
    assert not ended(result)
    assert isinstance(session.current_agent, CallReferenceTask)
    return agent


@pytest.mark.quarantine  # 2026-09-18: thanks "Jamie R" in words without recording the name, 3 in 5 runs on main; measured nightly
@pytest.mark.asyncio
async def test_last_answer_hands_the_closing_to_the_task(
    session, start_assistant, judge_llm
):
    # The interview asks nothing after the open question: the task asks for
    # the name, the interview never asks for a reference.
    await at_closing_task(session, start_assistant)
    result = await session.run(user_input="Jamie R.")
    show_response("Jamie R.", result)
    assert "Jamie" in (session.current_agent.representative_name or "")
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        judge_llm, intent="Asks for the call reference number for today's call."
    )


@pytest.mark.quarantine  # 2026-11-23: rep's name matching the caller's goes unrecorded, 6 in 8 runs on main; measured nightly
@pytest.mark.asyncio
async def test_first_name_alone_is_not_given_the_callers_initial(
    session, start_assistant, judge_llm
):
    # From a real call: the representative gave only a first name, the same as
    # the caller's, and was recorded with the initial from the caller's own
    # introduction earlier on the line.
    introduced = [
        ("user", "Thank you for calling. Who am I speaking with?"),
        ("assistant", "Hi, this is Megan, last initial S as in sierra."),
        ("user", "Thanks, Megan. What can I help you with?"),
    ]
    await at_closing_task(
        session,
        start_assistant,
        introduced,
        caller_first_name="Megan",
        caller_last_initial="S",
    )
    result = await session.run(user_input="My name is Megan.")
    show_response("My name is Megan.", result)
    assert session.current_agent.representative_name == "Megan"
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Asks for the representative's last initial or the call reference. "
            "Does not state a last initial for the representative."
        ),
    )


@pytest.mark.asyncio
async def test_goodbye_while_reference_pending(session, start_assistant, judge_llm):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    user_input = (
        "Let me generate a reference for you. Anyway, that's everything, right? Bye."
    )
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    # The call stays up until the reference is confirmed or ruled out.
    assert not ended(result)
    assert agent.call_reference is None
    assert session.current_agent.current_reference is None
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent="Asks for the call reference number. Does not say goodbye or claim to have a reference.",
    )


@pytest.mark.asyncio
async def test_reference_is_read_back_and_confirmed_before_ending(
    session, start_assistant, judge_llm
):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")

    result = await session.run(user_input="Your reference number is AB73921.")
    show_response("Your reference number is AB73921.", result)
    result.expect.contains_function_call(name="update_call_reference")
    assert session.current_agent.current_reference == "AB73921"
    await result.expect.contains_message(role="assistant").judge(
        judge_llm, intent="Reads back the complete reference AB73921 to confirm it."
    )
    assert not ended(result)

    result = await session.run(user_input="That's correct. Goodbye.")
    show_response("That's correct. Goodbye.", result)
    result.expect.contains_function_call(name="confirm_call_reference")
    assert ended(result)
    assert agent.call_reference is not None
    assert agent.call_reference.reference == "AB73921"
    assert "Jamie" in (agent.call_reference.representative_name or "")


@pytest.mark.asyncio
async def test_name_and_reference_volunteered_together(
    session, start_assistant, judge_llm
):
    agent = await at_closing_task(session, start_assistant)
    user_input = "It's Jamie R, and your reference number for today is AB73921."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    # Both recorded from one utterance; the reference is read back once.
    assert not ended(result)
    assert "Jamie" in (session.current_agent.representative_name or "")
    assert session.current_agent.current_reference == "AB73921"
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent="Reads back the reference AB73921 and asks whether it is correct. Does not ask for the name or the reference as if they had not been given.",
    )

    result = await session.run(user_input="Correct.")
    show_response("Correct.", result)
    assert ended(result)
    assert (
        agent.call_reference is not None and agent.call_reference.reference == "AB73921"
    )


@pytest.mark.asyncio
async def test_cannot_verify_or_provide_reference(session, start_assistant):
    agent = await start_assistant()
    await replay(
        agent,
        [
            ("user", "I need the provider's tax ID before I can verify benefits."),
            (
                "assistant",
                "I don't have that information available. Can you proceed using the NPI?",
            ),
            ("user", "No. I cannot verify benefits without the tax ID."),
            ("assistant", "Understood. Is there a reference number for this call?"),
        ],
    )
    user_input = "No, I cannot provide a reference number because we could not verify the provider. Please call back with the tax ID. Goodbye."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    if not ended(result):
        # The task may still ask for the name before closing; answer it.
        assert isinstance(session.current_agent, CallReferenceTask)
        result = await session.run(user_input="Jamie R. Goodbye.")
        show_response("Jamie R. Goodbye.", result)
    assert ended(result)
    assert agent.call_reference is not None
    assert agent.call_reference.reference is None
    assert agent.call_reference.unavailable_statement


@pytest.mark.asyncio
async def test_reference_composed_from_name_and_date(
    session, start_assistant, judge_llm
):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    user_input = "We don't have numbers. The reference is my name and today's date."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    result.expect.contains_function_call(name="update_call_reference")
    today = agent.call_date
    recorded = session.current_agent.current_reference or ""
    assert "Jamie" in recorded and str(today.year) in recorded, recorded
    assert not ended(result)
    # The judge has no clock of its own: name the call date, or it judges
    # against whatever date it assumes.
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent=(
            "Reads back a reference made of the name Jamie R and today's date, "
            f"{today:%B} {today.day}, {today.year}, in any spoken form, and asks "
            "whether it is correct. Does not ask for a reference number."
        ),
    )

    result = await session.run(user_input="Yes, that's right. Bye.")
    show_response("Yes, that's right. Bye.", result)
    assert ended(result)
    assert agent.call_reference is not None and "Jamie" in (
        agent.call_reference.reference or ""
    )


@pytest.mark.asyncio
async def test_correction_after_read_back(session, start_assistant, judge_llm):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    await session.run(user_input="Your reference number is AB73921.")
    user_input = "No, sorry, it's AB73927. Seven at the end."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert session.current_agent.current_reference == "AB73927"
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        judge_llm, intent="Reads back AB73927 and asks whether it is correct."
    )

    result = await session.run(user_input="Correct, thanks.")
    show_response("Correct, thanks.", result)
    assert ended(result)
    assert (
        agent.call_reference is not None and agent.call_reference.reference == "AB73927"
    )


@pytest.mark.asyncio
async def test_waits_while_reference_is_generated(session, start_assistant, judge_llm):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    user_input = "Sure, let me pull that up for you, one moment."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert not ended(result)
    assert session.current_agent.current_reference is None
    for ev in result.events:
        assert not (
            ev.type == "function_call" and ev.item.name == "no_reference_available"
        )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm,
        intent="A brief acknowledgment that gives the representative time, such as 'Okay', 'Sure, take your time' or 'Sure, I'll wait for it'. Saying it will wait for the reference is fine; asking for it again is not. Does not ask a new question or say goodbye.",
    )

    result = await session.run(user_input="Okay, it's AB73921.")
    show_response("Okay, it's AB73921.", result)
    assert session.current_agent.current_reference == "AB73921"
    assert not ended(result)
    assert agent.call_reference is None
