"""Bug: the agent spoke stage directions aloud.

On call ttfa-20260905-000505 the agent emitted
"(Wait for the representative to proceed or ask for identity verification)"
three times. Everything the agent emits is synthesized, so a parenthetical
describing an action is read out to the representative.

HISTORY below is the exact chat context of the LLM request that produced the
first one, lifted from the Langfuse trace. Replaying the turns from scratch does
not reproduce the bug, so the test seeds that state and runs only the turn that
broke: the representative saying nothing more than "Okay.".

Failure rate on that context: 6/10 on google/gemma-4-31b-it, 0/10 on
openai/gpt-4.1. Re-check with LK_TEST_MODEL.
"""

import re

import pytest
from support.calls import replay

# A parenthetical long enough to be prose, so an aside like "(NPI)" doesn't trip it.
STAGE_DIRECTION = re.compile(r"[(\[][^)\]]{10,}[)\]]")

HISTORY = [
    ("user", "Hi."),
    ("user", "Jorge, I'm going to be your rep today. How can I help you?"),
    (
        "assistant",
        "This is Jordan. The initial of my last name is C. I'm calling to obtain "
        "some information about benefits for one of your members, please.",
    ),
    ("user", "Okay. Can you tell me please your your can you repeat your name?"),
    ("assistant", "This is Jordan. The initial of my last name is C."),
]

TRIGGER = "Okay."


@pytest.mark.asyncio
async def test_does_not_narrate_when_there_is_nothing_to_say(session, start_assistant):
    await replay(await start_assistant(), HISTORY)

    result = await session.run(user_input=TRIGGER)

    # Navigate with the framework; assert with a regex. Whether a reply is a
    # parenthetical stage direction is syntax, not intent, so judge() would add
    # an LLM call, its own flake, and its own opinion to a yes/no question.
    message = result.expect.next_event().is_message(role="assistant")
    text = message.event().item.text_content or ""

    assert not STAGE_DIRECTION.search(text), f"agent narrated instead of speaking: {text!r}"
