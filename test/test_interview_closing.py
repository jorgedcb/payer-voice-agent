"""Closing regressions from real calls, with synthetic reference values.

end_call runs CallReferenceTask before shutting down, so the reference is
collected by the task whatever the model does with the goodbye.
"""

import pytest

from call_reference import CallReferenceTask
from calls import replay, show_response


CLOSING = [
    ("assistant", "Are there any other limitations or exclusions?"),
]

LAST_ANSWER = "No, everything is based on medical necessity."


def ended(result) -> bool:
    return any(
        ev.type == "function_call_output" and ev.item.name == "end_call" and not ev.item.is_error
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
    user_input = "Let me generate a reference for you. Anyway, that's everything, right? Bye."
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    # The call stays up until the reference is confirmed or ruled out.
    assert not ended(result)
    assert agent.call_reference is None
    assert session.current_agent.current_reference is None
    await result.expect.contains_message(role="assistant").judge(
        llm, intent="Asks for the call reference number. Does not say goodbye or claim to have a reference."
    )


@pytest.mark.asyncio
async def test_reference_is_read_back_and_confirmed_before_ending(session, start_assistant, llm):
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
        llm, intent="Reads back the reference AB73921 and asks whether it is correct. Does not ask for the name or the reference as if they had not been given."
    )

    result = await session.run(user_input="Correct.")
    show_response("Correct.", result)
    assert ended(result)
    assert agent.call_reference is not None and agent.call_reference.reference == "AB73921"


@pytest.mark.asyncio
async def test_cannot_verify_or_provide_reference(session, start_assistant):
    agent = await start_assistant()
    await replay(agent, [
        ("user", "I need the provider's tax ID before I can verify benefits."),
        ("assistant", "I don't have that information available. Can you proceed using the NPI?"),
        ("user", "No. I cannot verify benefits without the tax ID."),
        ("assistant", "Understood. Is there a reference number for this call?"),
    ])
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
        llm, intent="Reads back a reference made of the name Jamie R and today's date, and asks whether it is correct. Does not ask for a reference number."
    )

    result = await session.run(user_input="Yes, that's right. Bye.")
    show_response("Yes, that's right. Bye.", result)
    assert ended(result)
    assert agent.call_reference is not None and "Jamie" in (agent.call_reference.reference or "")


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
    assert agent.call_reference is not None and agent.call_reference.reference == "AB73927"


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
        assert not (ev.type == "function_call" and ev.item.name == "no_reference_available")
    await result.expect.contains_message(role="assistant").judge(
        llm, intent="A brief acknowledgment of a few words, such as 'Okay' or 'Sure, take your time'. Does not repeat the request, ask a new question, or say goodbye."
    )

    result = await session.run(user_input="Okay, it's AB73921.")
    show_response("Okay, it's AB73921.", result)
    assert session.current_agent.current_reference == "AB73921"
    assert not ended(result)
    assert agent.call_reference is None
