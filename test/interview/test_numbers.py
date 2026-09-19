"""First-read identifier pacing, from a live call, with synthetic values.

The pacing is plain text: one spoken word per character, comma-separated. It
used to be LiveKit's <expr type="prosody" label="slow"> markup, which only the
inference TTS lowers to the provider's dialect; the ElevenLabs plugin sends text
verbatim, and on a live call the voice read the tag aloud.
"""

import re

import pytest
from support.calls import show_response


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What's a good callback number for you, Annie?", "2025550123"),
        ("What is the provider's NPI?", "1477583926"),
        ("What's that member's ID number?", "W812640935"),
    ],
)
async def test_identifiers_are_paced_one_character_at_a_time_on_first_read(
    session,
    start_assistant,
    llm,
    question,
    expected,
):
    await start_assistant(
        caller_first_name="Annie",
        callback_number="+12025550123",
        npi="1477583926",
        member_id="W812640935",
    )
    result = await session.run(user_input=question)
    show_response(question, result)
    message = result.expect.next_event().is_message(role="assistant")
    # The exact text handed to the TTS, before any transcript display cleanup.
    raw_response = message.event().item.raw_text_content
    print(f"Speech text: {raw_response}")
    # Anything angle-bracketed reaches the voice as spoken words.
    assert "<" not in raw_response and ">" not in raw_response, raw_response
    digit_words = dict(
        zip("zero one two three four five six seven eight nine".split(), "0123456789")
    )
    digit_words["oh"] = "0"
    tokens = re.findall(r"\d|[a-z]+", raw_response.lower())
    spoken_digits = "".join(
        token if token.isdecimal() else digit_words.get(token, "") for token in tokens
    )
    expected_digits = "".join(ch for ch in expected if ch.isdecimal())
    assert spoken_digits == expected_digits, spoken_digits
    # Each character is its own word: the digits are never run together as a number.
    assert not re.search(r"\d{2,}", raw_response), raw_response
    await message.judge(
        llm,
        intent=f"Reads the complete identifier {expected} one character at a time, preserving every digit and letter in order, as plain spoken words with no markup or tags. Digits may be written numerically or as words, and letters may be spoken directly or phonetically. Wording and punctuation may vary.",
    )
    result.expect.no_more_events()


@pytest.mark.parametrize(
    "dob,spoken_date",
    [
        ("1987-02-16", "February 16, 1987"),
        ("2019-03-04", "March 4, 2019"),
        ("2000-02-29", "February 29, 2000"),
    ],
)
async def test_date_of_birth_is_read_naturally(
    session,
    start_assistant,
    llm,
    dob,
    spoken_date,
):
    await start_assistant(member_dob=dob)
    question = "What is the patient's date of birth?"
    result = await session.run(user_input=question)
    show_response(question, result)
    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=f"States the date of birth as {spoken_date}, using a spoken month name and a natural day and year rather than reading individual digits or an ISO date string. Preserve the exact month, day, and year; different natural wording is acceptable.",
        )
    )
    result.expect.no_more_events()


async def test_caller_name_is_spelled_with_the_phonetic_alphabet(
    session,
    start_assistant,
    llm,
):
    # On a live call, asked "how do you spell your name?", the agent said "A, n, n, i, e"
    # and the representative had to ask again, then prompted "a as in alpha".
    await start_assistant(caller_first_name="Annie", caller_last_initial="S")
    question = "Annie, how do you spell your name?"
    result = await session.run(user_input=question)
    show_response(question, result)
    message = result.expect.next_event().is_message(role="assistant")
    text = message.event().item.text_content.lower()
    for word in ("alpha", "november", "india", "echo"):
        assert word in text, f"letter not given phonetically: {text!r}"
    assert (
        text.index("alpha")
        < text.index("november")
        < text.index("india")
        < text.index("echo")
    )
    result.expect.no_more_events()
