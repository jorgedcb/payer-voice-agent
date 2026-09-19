"""Confirmation regressions from [call ID removed].

At 3:06 the caller echoed a correct callback read-back; the representative then
announced a robocall disconnect.
Langfuse generation [trace ID removed] used openai/gpt-4.1. Test the failure
class with an entirely synthetic conversation and the current production prompt.
These measure text/model behavior, not audio delivery or hang-up rates.
"""

import re

import pytest

from support.calls import replay, show_response


# Entirely synthetic conversation; no private transcript or patient data is sent
# to inference. The real Langfuse generation above identifies the failure only.
HISTORY = [
    ("user", "For provider services, press one."),
    ("user", "Please hold for the next available representative."),
    ("user", "Provider services, this is Taylor. Who am I speaking with?"),
    ("assistant", "My name is Annie, last initial S."),
    ("user", "Are you calling from an office or a facility?"),
    ("assistant", "A facility."),
    ("user", "What number can we reach you at if we get disconnected?"),
    ("assistant", "Two zero two, five five five, zero one two three. No extension."),
]


@pytest.mark.parametrize("question", [
    "Let me check that I have this right: two zero two, five five five, zero one two three. No extension, right?",
    "So that's 202-555-0123, no extension, correct?",
    "And you said Annie, last initial S, calling from a facility, correct?",
])
async def test_correct_readback_gets_only_a_brief_confirmation(
    session, start_assistant, llm, question,
):
    agent = await start_assistant(
        caller_first_name="Annie", caller_last_initial="S",
        callback_number="2025550123", npi_type="facility",
    )
    await replay(agent, HISTORY)
    result = await session.run(user_input=question)
    show_response(question, result)
    message = result.expect.next_event().is_message(role="assistant")
    speech = message.event().item.raw_text_content
    assert len(speech.split()) <= 6, speech
    assert not re.search(r"\d|\b(zero|one|two|three|four|five|six|seven|eight|nine)\b", speech, re.I), speech
    await message.judge(
        llm,
        intent=(
            "Only briefly affirms the representative's correct read-back, such as "
            "'That's correct.' or 'Yes, that's right.' Does not repeat any of the "
            "confirmed details, offer to repeat them, ask a question, or introduce "
            "a new topic."
        ),
    )
    result.expect.no_more_events()


@pytest.mark.parametrize("question,intent", [
    (
        "So the callback is 202-555-0124, direct line, correct?",
        "Corrects the callback to 2025550123 (or corrects only the last digit to "
        "three). Does not affirm the incorrect number.",
    ),
    (
        "So that's 202-555-0123, extension four, correct?",
        "Corrects only the extension: there is no extension, it is a direct line. "
        "Does not repeat the correct callback number or agree to extension four.",
    ),
    (
        "Can you repeat the callback number slowly?",
        "Repeats the complete callback 2025550123 one digit at a time as spoken "
        "words. Does not merely say yes or that's correct.",
    ),
    (
        "Is that two zero two, five five five? I missed the last four digits.",
        "Supplies the missing last four digits zero one two three. Does not "
        "merely confirm the incomplete number as if nothing were missing.",
    ),
    (
        "Annie, last initial S, correct? Could you spell your first name for me?",
        "Spells Annie using the phonetic alphabet (alpha, november, november, "
        "india, echo). Separately giving the last initial S for sierra is also "
        "acceptable and does not change the first-name spelling. Does not stop "
        "at a brief confirmation and ignore the explicit spelling request.",
    ),
])
async def test_confirmation_still_handles_corrections_and_repeat_requests(
    session, start_assistant, llm, question, intent,
):
    agent = await start_assistant(
        caller_first_name="Annie", caller_last_initial="S",
        callback_number="2025550123", npi_type="facility",
    )
    await replay(agent, HISTORY)
    result = await session.run(user_input=question)
    show_response(question, result)
    await result.expect.next_event().is_message(role="assistant").judge(llm, intent=intent)
    result.expect.no_more_events()
