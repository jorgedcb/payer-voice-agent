"""A member ID's letters, which the keypad cannot send.

Keyed, a member ID with letters arrives as its digits alone. Some lines want
exactly that ("the numbers after the letters"); others need the letters, and
the agent kept keying the digits after the line had said so:

- Cigna's provider line, 2026-11 (three calls): a conversational assistant.
  It read the keyed digits back and asked for "any letter at the start"; the
  agent said yes, and the assistant found no member and ended the call. The
  plan's own eligibility check had matched the same ID and date of birth.
- Anthem's assistants, 2026-11 (several calls, directly and after BlueCard's
  transfer): asked for the ID again after finding no member, the agent keyed
  the same digits again, or said the ID without the letter inside it.
- A Blue plan's line after a BlueCard transfer, 2026-11: asked for "the member
  ID including any letters", the agent keyed the digits.

The lines' words are from those calls; identifiers are synthetic.
"""

import re

import pytest
from support.calls import (
    BCBSM_CALL,
    BCBSM_PATIENT,
    UHC_CALL,
    UHC_IVR_INSTRUCTIONS,
    UHC_MEMBER_ID_QUESTION,
    UHC_PATIENT,
    replay,
    show_response,
    spoken_digits,
)
from support.navigator import hears, keyed, presses, spoken

from prompts.formatting import NATO_ALPHABET

# Every letter's NATO word, in either spelling of J and X.
_NATO_LETTER = {word.upper(): letter.upper() for letter, word in NATO_ALPHABET.items()}
_NATO_LETTER |= {"JULIET": "J", "XRAY": "X"}
# Words that join a letter to its NATO word: "W as in Whiskey", "W for Whiskey".
_JOINERS = {"AS", "IN", "FOR", "LIKE"}


def characters(text: str) -> str:
    """The letters and digits said, in order, however they were written.

    A lone letter, its NATO word, a digit or a digit word each count once; a
    letter said with its NATO word ("W as in Whiskey", "W for Whiskey") counts
    once. Other words count as nothing.
    """
    out: list[str] = []
    joined = False
    for word in re.findall(r"[A-Z]+|\d", text.upper().replace("-", "")):
        if word in _JOINERS:
            joined = bool(out) and out[-1].isalpha()
            continue
        letter = _NATO_LETTER.get(word) or (
            word if len(word) == 1 and word.isalpha() else None
        )
        if letter:
            if not (joined and word in _NATO_LETTER and out[-1] == letter):
                out.append(letter)
        elif digits := spoken_digits(word.lower()):
            out.append(digits)
        joined = False
    return "".join(out)


def gives_the_whole_id(result, member_id: str) -> bool:
    """The whole ID said aloud, letters where they belong. Keys cannot carry
    them, so a turn that keys is a miss however it keys."""
    return not presses(result) and member_id in characters(spoken(result))


# Cigna --------------------------------------------------------------------

CIGNA = {
    "member_name": "Daniel Brenner",
    "member_id": "U3820571946",
    "member_date_of_birth": "1988-07-15",
    "tax_id": "84-2719035",
    "service_type": "physical_therapy",
}
CIGNA_DIGITS = CIGNA["member_id"][1:]

CIGNA_GREETING = (
    "Hello, and thank you for calling Cigna Healthcare. You have reached our "
    "provider service line. If you are a customer, please hang up and call the "
    "number on your ID card. I am your AI assistant. If this is a true medical "
    "emergency, please hang up and dial nine one one. To help us provide the best "
    "service, this call may be monitored or recorded. How can I help you today?"
)
CIGNA_MEMBER_QUESTION = (
    "Thanks. I've got the office. Now give me the patient's member ID or their "
    "nine digit Social Security number, if that's what you have."
)
# Two of the calls' wordings: the letter asked about after the question, and
# before it.
CIGNA_READBACKS = [
    (
        "Okay. I have three eight two zero five seven one nine four six and July "
        "fifteenth nineteen eighty eight. Are both of those correct? And if that "
        "was a member ID, include any letter at the start if it has one."
    ),
    (
        "Okay. I have three eight two zero five seven one nine four six, and the "
        "date of birth as July fifteenth nineteen eighty eight. If that was a "
        "member ID, make sure it includes any starting letter if there is one. Are "
        "both of those correct?"
    ),
]
CIGNA_NO_MATCH = (
    "I'm still not finding a match. Do you have the patient's member ID? If not, "
    "please give me the patient's Social Security number and date of birth one "
    "more time."
)

TO_CIGNA_READBACK = [
    ("user", CIGNA_GREETING),
    ("speak", "benefits and eligibility"),
    (
        "user",
        (
            "For benefits and eligibility, I just need to place you first. Are you "
            "calling as a member or a provider."
        ),
    ),
    ("speak", "provider"),
    (
        "user",
        (
            "Okay. Provider line then. And to get to the right eligibility record, I "
            "need to verify the office first. What's your nine digit tax ID?"
        ),
    ),
    ("send_dtmf_events", spoken_digits(CIGNA["tax_id"])),
    ("user", CIGNA_MEMBER_QUESTION),
    ("send_dtmf_events", CIGNA_DIGITS),
    ("user", "Got it. And what's the patient's date of birth?"),
    ("send_dtmf_events", "07151988"),
    ("user", "Alright. I have that."),
    ("wait", None),
]


def gives_the_letter(result) -> bool:
    """The U said aloud, alone or with the rest of the ID."""
    return not presses(result) and "U" in characters(spoken(result))


@pytest.mark.parametrize(
    "readback", CIGNA_READBACKS, ids=["letter-after-question", "letter-before-question"]
)
async def test_cigna_readback_without_the_letter_gets_it(
    session, start_navigator, readback
) -> None:
    """The turn the calls were lost on. A bare yes tells the assistant the digits
    were the whole ID; a yes that goes on to give the letter answers both of its
    questions."""
    navigator = await start_navigator(**CIGNA)
    await replay(navigator, TO_CIGNA_READBACK)

    result = await hears(session, readback)
    show_response(readback, result)

    assert gives_the_letter(result), (
        f"did not give the letter: {spoken(result)!r} {presses(result)}"
    )


async def test_cigna_no_match_after_keyed_digits_gets_the_whole_id(
    session, start_navigator
) -> None:
    navigator = await start_navigator(**CIGNA)
    await replay(
        navigator,
        [
            *TO_CIGNA_READBACK,
            ("user", CIGNA_READBACKS[0]),
            ("speak", "Yes"),
            ("user", "Okay. Thanks for confirming."),
            ("wait", None),
        ],
    )

    result = await hears(session, CIGNA_NO_MATCH)
    show_response(CIGNA_NO_MATCH, result)

    assert gives_the_whole_id(result, CIGNA["member_id"]), (
        f"did not give the whole member ID: {spoken(result)!r} {presses(result)}"
    )


async def test_cigna_readback_with_the_letter_is_confirmed(
    session, start_navigator
) -> None:
    """The whole ID read back is the ID: a plain yes, nothing re-entered."""
    navigator = await start_navigator(**CIGNA)
    await replay(navigator, TO_CIGNA_READBACK)

    readback = (
        "Okay. I have U three eight two zero five seven one nine four six and July "
        "fifteenth nineteen eighty eight. Are both of those correct?"
    )
    result = await hears(session, readback)
    show_response(readback, result)

    assert presses(result) == [], f"keyed in answer to a question: {presses(result)}"
    assert spoken(result).lower().strip(" .!").startswith("yes"), (
        f"did not confirm the whole ID: {spoken(result)!r}"
    )


# Anthem, after BlueCard's transfer ---------------------------------------

ANTHEM = {
    "member_name": "Priya Holloway",
    # A letter inside the ID as well as the prefix, as on the call. Keyed, the
    # digits on both sides of it went in.
    "member_id": "CQP730W18265",
    "member_date_of_birth": "2015-02-11",
    "service_type": "physical_therapy",
}
ANTHEM_NOT_FOUND = "We couldn't find that member on file. Can you repeat the member ID?"


async def test_anthem_not_found_after_keyed_digits_gets_the_whole_id(
    session, start_navigator
) -> None:
    """Keyed again, the same digits find nothing again; the letters are what was
    missing, the one inside the ID included."""
    navigator = await start_navigator(**ANTHEM)
    await replay(
        navigator,
        [
            (
                "user",
                (
                    "What are the first three characters of the patient's member "
                    "ID? You can tell me the letters or use the phonetic alphabet, "
                    "such as foxtrot Zulu Juliet four f c j."
                ),
            ),
            ("speak", "C Q P"),
            ("user", "That was c q p. Is that correct?"),
            ("speak", "Yes"),
            (
                "user",
                "Are you calling for precertification, benefit and eligibility, or both?",
            ),
            ("speak", "Benefit and eligibility"),
            (
                "user",
                (
                    "Thank you. Please hold while your call is being connected to the "
                    "appropriate Blue Cross Blue Shield plan."
                ),
            ),
            ("wait", None),
            (
                "user",
                (
                    "Thank you for calling Anthem Blue Cross and Blue Shield. I am "
                    "your AI virtual assistant. Calls may be monitored or recorded "
                    "for quality purposes. Are you calling as a member or a provider?"
                ),
            ),
            ("speak", "Provider"),
            ("user", "Please say or enter your provider NPI or tax ID."),
            ("send_dtmf_events", "0000000000"),
            ("user", "I've located your details. Please give me the member ID."),
            ("send_dtmf_events", "73018265"),
        ],
    )

    result = await hears(session, ANTHEM_NOT_FOUND)
    show_response(ANTHEM_NOT_FOUND, result)

    assert gives_the_whole_id(result, ANTHEM["member_id"]), (
        f"did not give the whole member ID: {spoken(result)!r} {presses(result)}"
    )


# A Blue plan after BlueCard's transfer ------------------------------------

BLUE_PLAN = {
    "member_name": "Owen Castellanos",
    "member_id": "QKT604W18375",
    "member_date_of_birth": "2023-09-24",
    "service_type": "physical_therapy",
}
TO_BLUE_PLAN_MEMBER_QUESTION = [
    (
        "user",
        (
            "Payments for services rendered by a nonparticipating provider are "
            "generally sent to the member except when federal or state mandates "
            "apply or negotiated agreements are in place. Now tell me your... enter "
            "your provider ID."
        ),
    ),
    ("send_dtmf_events", "0000000000"),
]


async def test_member_id_including_letters_is_said(session, start_navigator) -> None:
    navigator = await start_navigator(**BLUE_PLAN)
    await replay(navigator, TO_BLUE_PLAN_MEMBER_QUESTION)

    question = "Now tell me the member ID including any letters."
    result = await hears(session, question)
    show_response(question, result)

    assert gives_the_whole_id(result, BLUE_PLAN["member_id"]), (
        f"did not give the whole member ID: {spoken(result)!r} {presses(result)}"
    )


async def test_numbers_after_the_letters_are_keyed(session, start_navigator) -> None:
    """A line that asks for the digits alone gets them on the keypad, as before."""
    patient = {**BLUE_PLAN, "member_id": "JRM482105937"}
    navigator = await start_navigator(**patient)
    await replay(navigator, TO_BLUE_PLAN_MEMBER_QUESTION)

    question = (
        "Eligibility and benefits. Say or enter just the numbers after the letters "
        "in the subscriber ID."
    )
    result = await hears(session, question)
    show_response(question, result)

    assert spoken(result) == "", f"spoke instead of keying: {spoken(result)!r}"
    assert keyed(result) == "482105937", f"keyed {presses(result)}"


# Lines that take the digits ----------------------------------------------


async def test_digits_readback_on_a_line_that_asked_for_them_is_confirmed(
    session, start_navigator
) -> None:
    """BCBS Michigan asked for the numeric portion and read it back: right as it
    is, so a yes, not the letters it did not ask for."""
    readback = "That's seven one two four zero eight three five six. Is that right?"
    navigator = await start_navigator(**BCBSM_PATIENT)
    await replay(navigator, BCBSM_CALL[: BCBSM_CALL.index(("user", readback))])

    result = await hears(session, readback)
    show_response(readback, result)

    assert presses(result) == [], f"keyed in answer to a question: {presses(result)}"
    assert spoken(result).lower().strip(" .!") == "yes", spoken(result)


async def test_numeric_member_id_not_found_gets_no_letters(
    session, start_navigator
) -> None:
    """An ID with no letters lost nothing on the keypad, so the letters rule does
    not apply: the same digits go in again, with nothing added. Keyed or said is
    left open; main said them 3 of 4 times here before this rule existed."""
    member_id = UHC_PATIENT["member_id"]
    navigator = await start_navigator(
        **UHC_PATIENT, ivr_instructions=UHC_IVR_INSTRUCTIONS
    )
    await replay(
        navigator,
        [*UHC_CALL, ("user", UHC_MEMBER_ID_QUESTION), ("send_dtmf_events", member_id)],
    )

    not_found = "I couldn't find that member. Please say or enter the member ID again."
    result = await hears(session, not_found)
    show_response(not_found, result)

    given = keyed(result) or characters(spoken(result))
    assert given == member_id, f"gave {given!r}: {spoken(result)!r} {presses(result)}"
