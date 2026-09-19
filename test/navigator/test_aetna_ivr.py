"""Aetna's IVR route from the 2026-09-10 transcript, with synthetic identifiers.

One session keeps the model's own decisions in history throughout the route.
Transcript fragments are grouped into questions/announcements; this tests the
decisions, not audio timing or whether Aetna follows these branches on a live call.
"""

import json

from support.calls import AETNA_PATIENT, called, show_response

AETNA_IVR_INSTRUCTIONS = (
    "If the member ID starts with W, enter only the numeric portion when using the keypad. "
    "At the initial fax-only offer, stay silent so the IVR continues. "
    "Decline procedure-code lookup. "
    "When asked whether you want benefit details, stay silent through the repeated "
    "prompts until the representative option is offered, then select it. "
    "If asked whether there are any other patients, choose No."
)


async def test_aetna_ivr_route_with_custom_instructions(
    session, start_navigator
) -> None:
    await start_navigator(**AETNA_PATIENT, ivr_instructions=AETNA_IVR_INSTRUCTIONS)

    # None means silence; a string means exactly those keypad digits. Expected
    # actions are assertions only: the model receives just the IVR's words.
    steps = [
        (
            "Thank you for calling Aetna's dedicated provider service center. "
            "To improve our service, your call will be monitored and recorded. "
            "By continuing with this call, you understand, accept, and agree that "
            "the information communicated by Aetna is not an offer of payment "
            "does not guarantee coverage or payment, and is subject to all benefit "
            "plan terms and conditions. Including member eligibility at the time "
            "of service. Please say or enter your NPI or tax ID.",
            "1477583926",
        ),
        ("Just a moment.", None),
        (
            "Thanks. I found your record. Let's get started. Say claims or press one. "
            "Coverage and benefits. Or press two, precertification or press three. "
            "Contact information or press four. Appeal inquiries or press five "
            "or join the network. Or press six.",
            "2",
        ),
        (
            "Coverage and benefits. Please say or enter the patient's Aetna ID.",
            "812640935",
        ),
        ("One moment, please.", None),
        ("Now enter or say the patient's date of birth.", "05142018"),
        (
            "One moment, please. The patient is Rashid Amari. Correct? "
            "Say yes or press one. No, two.",
            "1",
        ),
        (
            "To receive the information via fax, say have it faxed. Or press two.",
            None,
        ),
        (
            "Would you like to check benefits using a procedure code? "
            "Say yes or press one. No, two.",
            "2",
        ),
        (
            "The reference number for all the information you'll hear on this call "
            "is a v a one two three four five six. Again, the reference number "
            "is a v a one two three four five six.",
            None,
        ),
        (
            "This patient's coverage is active. They have an Aetna open access "
            "managed choice plan, which is commercially funded. This plan does "
            "not require referrals from the member's primary care physician. "
            "The effective date for the patient's plan is August first twenty twenty six.",
            None,
        ),
        (
            "The patient's original effective date is August first twenty twenty six.",
            None,
        ),
        ("Their group number is one two three four five six.", None),
        (
            "Benefit details, including deductibles, co pays, and coinsurance are "
            "available. Do you want benefit details for this patient? "
            "Say yes or press one. No or press two.",
            None,
        ),
        (
            "Sorry. I didn't hear you. Do you want benefit details? "
            "Say yes or press one. No or press two. "
            "For more information, say help or press star.",
            None,
        ),
        (
            "Sorry. I still didn't hear you. If you want benefit details, including "
            "deductibles, co pays, and coinsurance for this patient, press one. "
            "If not, press two. For more information, press star. "
            "Or to speak with a representative, press zero.",
            "0",
        ),
        (
            "Are there any other patients I can help you with? "
            "Say yes or press one. No. Two.",
            "2",
        ),
    ]

    for user_input, keys in steps:
        result = await session.run(user_input=user_input)
        show_response(user_input, result)

        assert not called(result, "representative_answered"), (
            f"premature handoff before a human spoke; IVR: {user_input}"
        )
        assert not called(result, "hold_for_representative"), (
            f"left the menu while it was still asking; IVR: {user_input}"
        )
        assert not any(
            event.type == "message"
            and event.item.role == "assistant"
            and event.item.text_content
            for event in result.events
        ), "navigator spoke during the IVR route"
        tool_calls = [
            (event.item.name, json.loads(event.item.arguments))
            for event in result.events
            if event.type == "function_call"
        ]
        expected = (
            [("wait", {})]
            if keys is None
            else [("send_dtmf_events", {"events": list(keys)})]
        )
        # DTMF returns a tool result, so the model may follow it with wait.
        allowed = [expected] if keys is None else [expected, expected + [("wait", {})]]
        assert tool_calls in allowed, (
            f"IVR: {user_input}\nExpected: {expected}\nGot: {tool_calls}"
        )

    # The supplied recording ends outside business hours. That is an exit,
    # not a representative greeting, even though its UI labeled it as one.
    user_input = (
        "Our business hours are Monday through Friday, eight AM to five PM. "
        "Please try your call again during business hours."
    )
    result = await session.run(user_input=user_input)
    show_response(user_input, result)
    result.expect.contains_function_call(name="end_call")
    assert not any(event.type == "agent_handoff" for event in result.events)
    assert all(
        event.item.name == "end_call"
        for event in result.events
        if event.type == "function_call"
    )
