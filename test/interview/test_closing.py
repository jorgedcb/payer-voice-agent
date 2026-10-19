"""Closing regressions from real calls, with synthetic reference values.

end_call runs CallReferenceTask before shutting down, so the reference is
collected by the task whatever the model does with the goodbye.
"""

import pytest
from support.calls import (
    AETNA_ALL_TOPICS_ANSWER,
    AETNA_ALL_TOPICS_CALL,
    AETNA_ALL_TOPICS_PATIENT,
    AETNA_OUT_OF_STATE_ANSWER,
    AETNA_OUT_OF_STATE_CALL,
    AETNA_OUT_OF_STATE_PATIENT,
    AETNA_PT_CLINIC,
    AETNA_VISIT_LIMITS_ANSWER,
    AETNA_VISIT_LIMITS_CALL,
    AETNA_VISIT_LIMITS_PATIENT,
    assert_yields_turn,
    called,
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
        ),
    ],
)
async def test_representative_goodbye_during_transfer_does_not_start_closing(
    session,
    start_assistant,
    llm,
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
    await assert_yields_turn(result, llm, after=farewell)

    user_input = (
        "Hello, this is Morgan with the dedicated benefits team. How can I help you?"
    )
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert not called(result, "end_call")
    await result.expect.contains_message(role="assistant").judge(
        llm,
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
    assert called(result, "end_call"), (
        "An explicitly failed transfer must not leave the agent waiting."
    )


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
    session, start_assistant, llm, patient, history, last_answer, open_topics
):
    # Aetna calls on 2026-10-13 and 2026-10-14 asked about other limitations or
    # exclusions at this turn and went on to end the call: the medical-necessity
    # review was never asked, nor the other topics after it in the order.
    agent = await start_assistant(**AETNA_PT_CLINIC, **patient)
    await replay(agent, history)

    result = await session.run(user_input=last_answer)
    show_response(last_answer, result)

    assert not called(result, "end_call"), (
        "Benefit topics are still open; the call must not start closing."
    )
    await result.expect.contains_message(role="assistant").judge(
        llm,
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
    session, start_assistant, llm
):
    # The same clinic on another Aetna call on 2026-10-14, where every topic was
    # covered: the closing question is the right next turn, not more questions.
    agent = await start_assistant(**AETNA_PT_CLINIC, **AETNA_ALL_TOPICS_PATIENT)
    await replay(agent, AETNA_ALL_TOPICS_CALL)

    result = await session.run(user_input=AETNA_ALL_TOPICS_ANSWER)
    show_response(AETNA_ALL_TOPICS_ANSWER, result)

    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=(
            "Asks whether there are any other limitations or exclusions, in any "
            "wording; a brief acknowledgment may come first. It does not ask again "
            "about a benefit the representative already answered, such as visit "
            "limits, the out-of-pocket maximum, the deductible, medical necessity "
            "or the reimbursement rate."
        ),
    )


def ended(result) -> bool:
    return any(
        ev.type == "function_call_output"
        and ev.item.name == "end_call"
        and not ev.item.is_error
        for ev in result.events
    )


async def at_closing_task(session, start_assistant):
    """Run to the point where the last interview answer made end_call start the task."""
    agent = await start_assistant()
    await replay(agent, CLOSING)
    result = await session.run(user_input=LAST_ANSWER)
    show_response(LAST_ANSWER, result)
    result.expect.contains_function_call(name="end_call")
    assert not ended(result)
    assert isinstance(session.current_agent, CallReferenceTask)
    return agent


@pytest.mark.quarantine  # 2026-09-18: thanks "Jamie R" in words without recording the name, 3 in 5 runs on main; measured nightly
@pytest.mark.asyncio
async def test_last_answer_hands_the_closing_to_the_task(session, start_assistant, llm):
    # The interview asks nothing after the open question: the task asks for
    # the name, the interview never asks for a reference.
    await at_closing_task(session, start_assistant)
    result = await session.run(user_input="Jamie R.")
    show_response("Jamie R.", result)
    assert "Jamie" in (session.current_agent.representative_name or "")
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        llm, intent="Asks for the call reference number for today's call."
    )


@pytest.mark.asyncio
async def test_goodbye_while_reference_pending(session, start_assistant, llm):
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
        llm,
        intent="Asks for the call reference number. Does not say goodbye or claim to have a reference.",
    )


@pytest.mark.asyncio
async def test_reference_is_read_back_and_confirmed_before_ending(
    session, start_assistant, llm
):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")

    result = await session.run(user_input="Your reference number is AB73921.")
    show_response("Your reference number is AB73921.", result)
    result.expect.contains_function_call(name="update_call_reference")
    assert session.current_agent.current_reference == "AB73921"
    await result.expect.contains_message(role="assistant").judge(
        llm, intent="Reads back the complete reference AB73921 to confirm it."
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
async def test_name_and_reference_volunteered_together(session, start_assistant, llm):
    agent = await at_closing_task(session, start_assistant)
    user_input = "It's Jamie R, and your reference number for today is AB73921."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    # Both recorded from one utterance; the reference is read back once.
    assert not ended(result)
    assert "Jamie" in (session.current_agent.representative_name or "")
    assert session.current_agent.current_reference == "AB73921"
    await result.expect.contains_message(role="assistant").judge(
        llm,
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
async def test_reference_composed_from_name_and_date(session, start_assistant, llm):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    user_input = "We don't have numbers. The reference is my name and today's date."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    result.expect.contains_function_call(name="update_call_reference")
    recorded = session.current_agent.current_reference or ""
    assert "Jamie" in recorded and "2026" in recorded, recorded
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent="Reads back a reference made of the name Jamie R and today's date, and asks whether it is correct. Does not ask for a reference number.",
    )

    result = await session.run(user_input="Yes, that's right. Bye.")
    show_response("Yes, that's right. Bye.", result)
    assert ended(result)
    assert agent.call_reference is not None and "Jamie" in (
        agent.call_reference.reference or ""
    )


@pytest.mark.asyncio
async def test_correction_after_read_back(session, start_assistant, llm):
    agent = await at_closing_task(session, start_assistant)
    await session.run(user_input="Jamie R.")
    await session.run(user_input="Your reference number is AB73921.")
    user_input = "No, sorry, it's AB73927. Seven at the end."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    assert session.current_agent.current_reference == "AB73927"
    assert not ended(result)
    await result.expect.contains_message(role="assistant").judge(
        llm, intent="Reads back AB73927 and asks whether it is correct."
    )

    result = await session.run(user_input="Correct, thanks.")
    show_response("Correct, thanks.", result)
    assert ended(result)
    assert (
        agent.call_reference is not None and agent.call_reference.reference == "AB73927"
    )


@pytest.mark.asyncio
async def test_waits_while_reference_is_generated(session, start_assistant, llm):
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
        llm,
        intent="A brief acknowledgment of a few words, such as 'Okay' or 'Sure, take your time'. Does not repeat the request, ask a new question, or say goodbye.",
    )

    result = await session.run(user_input="Okay, it's AB73921.")
    show_response("Okay, it's AB73921.", result)
    assert session.current_agent.current_reference == "AB73921"
    assert not ended(result)
    assert agent.call_reference is None
