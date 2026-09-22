"""Real calls, as the model saw them, for replaying in tests.

Each call is the chat context at one turn, taken from the Langfuse trace: the
payer's utterances verbatim as STT delivered them, and the agent's own tool
calls in order. Identifiers are invented, since the calls carried real patients'.
"""

import json

from livekit.agents import ChatContext, llm


def called(result, name: str) -> bool:
    """Whether the agent called this tool during the turn."""
    return any(
        ev.type == "function_call" and ev.item.name == name for ev in result.events
    )


def assert_wait_is_exclusive(result) -> None:
    """Silence is an alternative to speech or another action, never an extra step."""
    if not called(result, "wait"):
        return
    actions = [ev for ev in result.events if ev.type == "function_call"]
    speech = [
        ev
        for ev in result.events
        if ev.type == "message" and ev.item.role == "assistant" and ev.item.text_content
    ]
    assert len(actions) == 1 and not speech, "wait must be the turn's only action."


async def assert_yields_turn(result, judge, *, after: str) -> None:
    """Accept silence or a brief acknowledgment without advancing or closing."""
    assert not called(result, "end_call"), "Waiting must not start closing."
    assert_wait_is_exclusive(result)
    # A model may acknowledge, call wait alone, or produce no speech.
    # Check every spoken message rather than pinning a tool choice or wording.
    for index, event in enumerate(result.events):
        if (
            event.type == "message"
            and event.item.role == "assistant"
            and event.item.text_content
        ):
            await (
                result.expect[index]
                .is_message(role="assistant")
                .judge(
                    judge,
                    intent=(
                        f"The representative just said {after!r}. In this context, the reply is "
                        "A brief conversational acknowledgment or courtesy that gives the "
                        "representative time to continue. A word or a short sentence is valid; "
                        "no particular wording is required. Does not ask a question, request "
                        "a call reference, start a new topic, claim verification is complete, "
                        "announce disconnection, or narrate stage directions."
                    ),
                )
            )


def handoff_opening(result) -> str:
    """The opening the navigator wrote into its handoff call.

    Consumes the handoff's own event, so the caller goes on asserting from the
    tool result that follows it.
    """
    call = result.expect.next_event().is_function_call(name="representative_answered")
    return json.loads(call.event().item.arguments)["opening"]


def turn_settings(session):
    """What the SDK resolved for the agent now running: session default, agent override."""
    activity = session.current_agent._get_activity_or_raise()
    return activity.endpointing_opts, activity.preemptive_generation_opts["enabled"]


def show_response(user_input, result):
    """Print a test turn's replies and tool activity; use pytest -s to see passes."""
    print(f"\nRepresentative: {user_input}")
    for event in result.events:
        item = getattr(event, "item", None)
        if item is None:
            continue
        if item.type == "message":
            print(f"Agent: {item.text_content}")
        elif item.type == "function_call":
            print(f"Tool call: {item.name}({item.arguments})")
        elif item.type == "function_call_output":
            print(f"Tool result: {item.output}")


# Aetna provider line, 2026-09-06, at the turn the menu
# read the patient back. The identifiers are synthetic stand-ins for what the
# agent said on the line; a test passes them to the navigator so the prompt's
# THIS CALL matches the replayed turns.
AETNA_PATIENT = {
    "member_name": "Rashid Amari",
    "member_id": "W812640935",
    "member_dob": "2018-05-14",
    "npi": "1477583926",
}

AETNA_CALL = [
    (
        "user",
        (
            "Thank you for calling Aetna's dedicated provider service center. To improve "
            "our service your call will be monitored and recorded. By continuing with this "
            "call, you understand, accept, and agree that the information communicated by "
            "Aetna is not an offer of payment does not guarantee coverage or payment, and "
            "is subject to all benefit plan terms and conditions. Including member "
            "eligibility at the time of service. Please say or enter your NPI or tax ID."
        ),
    ),
    ("speak", "1 4 7 7 5 8 3 9 2 6"),
    (
        "user",
        (
            "Just a moment. Thanks. I found your record. Let's get started. Say claims, "
            "coverage and benefits, precertification, contact information, a appeal "
            "inquiries, or join the network."
        ),
    ),
    ("speak", "coverage and benefits"),
    ("user", "Coverage and benefits. Please say or enter the patient's Aetna ID."),
    ("speak", "W 8 1 2 6 4 0 9 3 5"),
    ("user", "One moment, please."),
    ("wait", None),
    ("user", "And the patient's date of birth?"),
    ("speak", "May 14, 2018"),
]

# What STT wrote when the menu said "Rashid". The next turn on the real call.
AETNA_READBACK = "One moment, please. The patient is Richard Amari. Correct?"

# UnitedHealthcare provider line, 2026-09-22, up to the turn the virtual
# assistant asked for the member ID. It is a speech-first system: every prompt
# so far said what to say and none mentioned the keypad. The identifiers are
# synthetic stand-ins, numeric like the real ones.
UHC_PATIENT = {
    "member_id": "907264318",
    "member_dob": "1987-04-09",
    "npi": "1588694037",
}

# The operator note UHC calls carry. Two more, saying "no" to hearing the copay
# again and to checking another therapy, were removed on 2026-09-22: turning
# down more self-service is the navigator's job without being told.
UHC_IVR_INSTRUCTIONS = (
    "When they say, “If that’s all you need, you may hang up now,” respond with "
    "“representative.”"
)

UHC_CALL = [
    (
        "user",
        (
            "UnitedHealthcare. Your call may be monitored or recorded for quality or "
            "account security purposes. AI may assist. For quicker assistance, please "
            "have your NPI or tax ID number ready. You may also be asked for the member "
            "ID and date of birth. Having this information available will help you "
            "access self-service options and connect with a representative more quickly."
        ),
    ),
    ("wait", None),
    ("user", "For English, just remain on the line."),
    ("wait", None),
    (
        "user",
        (
            "I'm your virtual assistant at United Healthcare. If you're a member and "
            "need help with your health plan, say I'm a member. Otherwise, in a few "
            "words, tell me what are you calling about."
        ),
    ),
    ("speak", "benefits and eligibility"),
    ("user", "Are you calling as a health care professional?"),
    ("speak", "yes"),
    (
        "user",
        (
            "Sure. What type of benefit are you calling about? You can say medical, "
            "behavioral health, prescriptions, dental, or vision."
        ),
    ),
    ("speak", "medical"),
    (
        "user",
        (
            "In order to connect you to the right advocate, please specify what type "
            "of benefit you are calling about. You can say one of these options, "
            "medical, behavioral health, prescriptions, dental, or vision. What type "
            "of benefit are you calling about?"
        ),
    ),
    ("speak", "medical"),
    ("user", "Thanks."),
    ("wait", None),
]

# The next turn on the real call. The agent spoke the ID and the system misheard
# it twice, then failed the spoken NPI and tax ID the same way.
UHC_MEMBER_ID_QUESTION = (
    "Let's move forward with your benefit request. And what is the member ID?"
)

# The same line on another call that day, which got through authentication and
# into the automated benefits, up to the turn they were read out.
UHC_BENEFITS_CALL = [
    *UHC_CALL,
    ("user", UHC_MEMBER_ID_QUESTION),
    ("speak", "9 0 7 2 6 4 3 1 8"),
    ("user", "I heard nine zero seven two six four three one eight. Is that correct?"),
    ("speak", "yes"),
    (
        "user",
        (
            "What is the member's date of birth including the four digit year. For "
            "example, June nineteenth nineteen sixty seven."
        ),
    ),
    ("speak", "April 9, 1987"),
    (
        "user",
        (
            "Thank you. One moment while I pull up the account. Alright. I have the "
            "account in front of me now. One moment. I'm pulling that up now. Okay. "
            "What is your NPI?"
        ),
    ),
    ("speak", "1 5 8 8 6 9 4 0 3 7"),
    (
        "user",
        (
            "Thanks. I've got it. United's verification of a member's benefits is not "
            "a guarantee of payment, and United is not entering into a contract for "
            "payment of any amount by providing this information. Payments are only "
            "determined when claims are received and processed through the member's "
            "plan. Now what type of benefit are you calling about? For example, co "
            "pay, coinsurance, therapy benefits and limits, coordination of benefits, "
            "deductible, out of pocket, plan details, or PCP."
        ),
    ),
    ("speak", "therapy benefits and limits"),
    ("user", "Got it."),
    ("wait", None),
    ("user", "Just a moment while I check therapy benefits for you."),
    ("wait", None),
    (
        "user",
        (
            "Would you like co pay and coinsurance for in network, out of network, "
            "or in network tier one?"
        ),
    ),
    ("speak", "in network"),
    (
        "user",
        (
            "Okay. Would you like cardiac rehabilitation, occupational therapy, "
            "physical therapy, or speech therapy?"
        ),
    ),
    ("speak", "physical therapy"),
]

# The benefits read out, then two offers of more self-service. Saying yes to
# either keeps the call in the menu; on these calls the answer was "no" to both,
# then "representative" at the offer to hang up.
UHC_OFFER_TO_REPEAT = (
    "Okay. Here's the coinsurance information for physical therapy. Coinsurance is "
    "twenty percent after deductible has been met. I also found these details. "
    "Benefits allowed. Visits one hundred no dollar amount limit per calendar year "
    "limitation is combined for physical therapy, occupational therapy, and speech "
    "therapy for in network rehabilitative. Benefits remaining. Visits one hundred "
    "no dollar amount limit per remaining limitation is combined for physical "
    "therapy, occupational therapy, and speech therapy for in network "
    "rehabilitative. Visits three per remaining rehabilitative additional benefit "
    "for musculoskeletal pain management program. You need to hear that co pay and "
    "coinsurance again?"
)
UHC_OFFER_TO_CHECK_ANOTHER = (
    "Would you like to check another co pay or therapy for this member?"
)
UHC_OFFER_TO_HANG_UP = (
    "If that's all you need, you can hang up now. Otherwise, you can say check a "
    "different benefit, main menu, or connect to an advocate."
)


async def replay(under_test, history) -> None:
    """Seed the agent with a conversation exactly as the model would have seen it.

    A message role ("user" for the line, "assistant" for what the agent said)
    becomes a message. Anything else is one of the agent's own tool calls,
    with the empty result every navigator tool returns: `send_dtmf_events`
    takes the keys as a string, the other tools their text or None.
    """
    chat_ctx = ChatContext()
    for n, (kind, payload) in enumerate(history):
        if kind in ("user", "assistant", "system"):
            chat_ctx.add_message(role=kind, content=payload)
            continue
        call_id = f"call_{n}"
        if kind == "send_dtmf_events":
            arguments = json.dumps({"events": list(payload)})
        elif payload is not None:
            arguments = json.dumps({"text": payload})
        else:
            arguments = "{}"
        chat_ctx.insert(
            llm.FunctionCall(call_id=call_id, name=kind, arguments=arguments)
        )
        chat_ctx.insert(
            llm.FunctionCallOutput(
                call_id=call_id, name=kind, output="", is_error=False
            )
        )
    await under_test.update_chat_ctx(chat_ctx)
