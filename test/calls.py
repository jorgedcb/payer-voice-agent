"""Real calls, as the model saw them, for replaying in tests.

Each call is the chat context at one turn, taken from the Langfuse trace: the
payer's utterances verbatim as STT delivered them, and the agent's own tool
calls in order. Identifiers are invented, since the calls carried real patients'.
"""

import json

from livekit.agents import ChatContext, llm


def called(result, name: str) -> bool:
    """Whether the agent called this tool during the turn."""
    return any(ev.type == "function_call" and ev.item.name == name for ev in result.events)


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
    "member_dob": "2018-07-22",
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
    ("speak", "July 22, 2018"),
]

# What STT wrote when the menu said "Rashid". The next turn on the real call.
AETNA_READBACK = "One moment, please. The patient is Richard Amari. Correct?"


async def replay(under_test, history) -> None:
    """Seed the agent with a conversation exactly as the model would have seen it.

    A message role ("user" for the line, "assistant" for what the agent said)
    becomes a message. Anything else is one of the agent's own tool calls,
    with the empty result every navigator tool returns.
    """
    chat_ctx = ChatContext()
    for n, (kind, payload) in enumerate(history):
        if kind in ("user", "assistant", "system"):
            chat_ctx.add_message(role=kind, content=payload)
            continue
        call_id = f"call_{n}"
        arguments = json.dumps({"text": payload}) if payload is not None else "{}"
        chat_ctx.insert(llm.FunctionCall(call_id=call_id, name=kind, arguments=arguments))
        chat_ctx.insert(
            llm.FunctionCallOutput(call_id=call_id, name=kind, output="", is_error=False)
        )
    await under_test.update_chat_ctx(chat_ctx)
