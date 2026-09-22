"""What the navigator decides at a menu: wait, speak, press, or hand off.

Each test feeds one payer utterance as a turn and asserts the decision. These are
model behavior tests like the interview ones, so a failure is a prompt problem
until proven otherwise.
"""

import pytest
from support.calls import (
    AETNA_CALL,
    AETNA_PATIENT,
    UHC_CALL,
    UHC_IVR_INSTRUCTIONS,
    UHC_MEMBER_ID_QUESTION,
    UHC_PATIENT,
    replay,
    show_response,
)
from support.navigator import (
    assert_moved_to_the_queue,
    assert_waited,
    hears,
    presses,
    spoken,
)


async def test_press_menu_key(session, start_navigator) -> None:
    await start_navigator()

    result = await hears(
        session,
        (
            "Thank you for calling. For claims, press one. For benefits and "
            "eligibility, press two. For all other inquiries, press three."
        ),
    )

    assert presses(result) == [["2"]]
    assert spoken(result) == ""


async def test_say_or_enter_npi_uses_dtmf(session, start_navigator) -> None:
    """Opening turn of a live call, with a synthetic provider NPI."""
    npi = "1477583926"
    await start_navigator(
        npi=npi,
        ivr_instructions="If the member ID starts with W, enter only the numeric portion when using the keypad.",
    )
    user_input = (
        "Thank you for calling Aetna's dedicated provider service center. "
        "To improve our service, your call will be monitored and recorded. "
        "By continuing with this call, you understand, accept, and agree that "
        "the information communicated by Aetna is not an offer of payment "
        "does not guarantee coverage or payment, and is subject to all benefit "
        "plan terms and conditions. Including member eligibility at the time "
        "of service. Please say or enter your NPI or tax ID."
    )
    result = await hears(session, user_input)
    show_response(user_input, result)

    assert spoken(result) == "", (
        "keypad input was offered; the NPI should not be spoken"
    )
    assert presses(result) == [list(npi)]
    assert all(
        event.item.name == "send_dtmf_events"
        for event in result.events
        if event.type == "function_call"
    )


async def test_member_id_is_keyed_on_a_speech_menu(session, start_navigator) -> None:
    """The UHC call replayed to the member ID question, which never mentions keys.

    On the real call the agent spoke the ID and the system misheard it twice.
    Keyed, the digits arrive exactly as they are. Only the ID's own digits are
    asserted, with an optional pound to end the entry.
    """
    member_id = UHC_PATIENT["member_id"]
    navigator = await start_navigator(
        **UHC_PATIENT, ivr_instructions=UHC_IVR_INSTRUCTIONS
    )
    await replay(navigator, UHC_CALL)

    result = await hears(session, UHC_MEMBER_ID_QUESTION)
    show_response(UHC_MEMBER_ID_QUESTION, result)

    assert spoken(result) == "", f"spoke the member ID: {spoken(result)!r}"
    assert presses(result) in ([list(member_id)], [[*member_id, "#"]])


async def test_fax_offer_does_not_request_fax(session, start_navigator) -> None:
    """The fax offer heard two minutes into a live call must not select fax."""
    await start_navigator()
    user_input = "To receive the information via fax, say have it faxed. Or press two."
    result = await hears(session, user_input)
    show_response(user_input, result)

    assert not any("2" in sequence for sequence in presses(result)), (
        "pressed 2 to request a fax"
    )
    assert "fax" not in spoken(result).lower(), f"spoke about fax: {spoken(result)!r}"


async def test_fax_offer_custom_instruction_stays_silent(
    session, start_navigator
) -> None:
    await start_navigator(
        ivr_instructions="When Aetna offers to send the information by fax, stay silent so the IVR continues.",
    )
    user_input = "To receive the information via fax, say have it faxed. Or press two."
    result = await hears(session, user_input)
    show_response(user_input, result)

    result.expect.next_event().is_function_call(name="wait")
    result.expect.next_event().is_function_call_output()
    result.expect.no_more_events()


async def test_benefit_details_custom_instruction_declines(
    session, start_navigator
) -> None:
    """Aetna's menu three minutes into a live call, with instructions to skip it."""
    await start_navigator(
        ivr_instructions=(
            'When Aetna offers optional automated benefit details, choose "No." '
            "Continue navigating to a live representative."
        ),
    )
    user_input = (
        "Benefit details, including deductibles co pays, and coinsurance are also available. "
        "Do you want benefit details for this patient? "
        "Say yes or press one. No, or press two."
    )
    result = await hears(session, user_input)
    show_response(user_input, result)

    assert presses(result) == [["2"]]
    assert spoken(result) == ""


@pytest.mark.quarantine  # 2026-09-12: sometimes only calls `wait` here; measured nightly
async def test_enter_member_id_with_pound_in_one_call(session, start_navigator) -> None:
    await start_navigator()

    result = await hears(
        session, "Please enter the member's ID number, followed by the pound key."
    )

    # The sample ID is W123456789. What to do with the letter is the payer's
    # call (drop it, or key it as a digit), so only the fixed part is asserted:
    # the digits in order, the pound in the same call, at most one extra key.
    [sequence] = presses(result)
    assert sequence[-1] == "#"
    assert sequence[-10:-1] == list("123456789")
    assert len(sequence) <= 11, f"more than one key for the letter: {sequence}"


async def test_payer_notes_change_how_a_value_is_keyed(
    session, start_navigator
) -> None:
    # What a menu does with a letter is a fact about the payer. An operator
    # writes it down once, the dispatcher sends it, and the model follows it.
    await start_navigator(
        ivr_instructions="This payer's system wants the member ID's leading letter keyed as 9.",
    )

    result = await hears(
        session, "Please enter the member's ID number, followed by the pound key."
    )

    assert presses(result) == [["9"] + list("123456789") + ["#"]]


async def test_enter_date_of_birth(session, start_navigator) -> None:
    await start_navigator(member_dob="1990-01-23")

    result = await hears(
        session, "Please enter the patient's date of birth as an eight digit number."
    )

    assert presses(result) == [list("01231990")]


async def test_spoken_menu_is_answered_with_the_menu_words(
    session, start_navigator
) -> None:
    await start_navigator()

    result = await hears(
        session,
        (
            "In a few words, tell me what you are calling about. You can say "
            "things like claims, benefits and eligibility, or prior authorization."
        ),
    )

    assert presses(result) == []
    assert "benefits" in spoken(result).lower()


async def test_offer_of_a_representative_is_taken(session, start_navigator) -> None:
    await start_navigator()

    result = await hears(
        session,
        (
            "I can read the benefits to you now. To hear them, press one. "
            "To speak with a representative, press zero."
        ),
    )

    assert presses(result) == [["0"]]


@pytest.mark.quarantine  # 2026-09-18: presses a key on a read-back it should deny, 2 in 5 runs on main; measured nightly
async def test_aetna_readback_of_someone_else_is_denied(
    session, start_navigator
) -> None:
    # The Aetna call replayed to the readback turn, but the lookup landed on a
    # stranger, which a mis-said digit can cause. This one is not a judgment
    # call: confirming another patient's record is never right. (What to do
    # when the name is merely garbled is undecided; see test_capability.py.)
    navigator = await start_navigator(**AETNA_PATIENT)
    await replay(navigator, AETNA_CALL)

    result = await hears(
        session, "One moment, please. The patient is Emily Johnson. Correct?"
    )

    assert presses(result) == []
    assert spoken(result).lower().strip(".") == "no"


async def test_voicemail_hangs_up(session, start_navigator) -> None:
    # A mailbox answers the call like anyone else, so nothing upstream catches
    # it. Without this the navigator would wait through the greeting, the beep
    # and the silence until the job timed out.
    await start_navigator()

    result = await hears(
        session,
        "Hi, you've reached the office of Dr. Rivera. We can't take your call "
        "right now. Please leave your name and number after the tone.",
    )

    # The SDK's end_call shuts the session down and, on close, asks for the job
    # to delete the room. There is no job here, so teardown logs one "failed to
    # emit event close ... no job context" error. Noise, not a failure.
    result.expect.contains_function_call(name="end_call")
    assert presses(result) == []
    assert spoken(result) == ""


async def test_unfinished_menu_waits(session, start_navigator) -> None:
    await start_navigator()

    result = await hears(session, "Thank you for calling. For claims, press one. For")

    assert_waited(result)


async def test_transfer_announcement_moves_to_the_queue(
    session, start_navigator
) -> None:
    await start_navigator()

    result = await hears(
        session,
        "Please hold while I transfer your call to the next available representative.",
    )

    assert_moved_to_the_queue(result)


async def test_hold_announcement_moves_to_the_queue(session, start_navigator) -> None:
    await start_navigator()

    result = await hears(
        session,
        (
            "Your call is important to us. Please stay on the line and a "
            "representative will be with you shortly."
        ),
    )

    assert_moved_to_the_queue(result)
