"""Give the representative time, with silence or a brief acknowledgment."""

import pytest

from support.calls import assert_wait_is_exclusive, assert_yields_turn, called, replay, show_response


LOOKUP = [
    ("user", "The provider and member are verified. What do you need to check?"),
    ("assistant", "Is the provider in network for this member's plan?"),
    ("user", "Let me pull up the network information. Can you hold for a moment?"),
    ("assistant", "Sure."),
]


@pytest.mark.parametrize("update", [
    pytest.param("I'm still checking the information in my system.", id="computer-lookup"),
    pytest.param("I'm just entering the details. My computer is taking a little longer.", id="typing"),
    pytest.param("And the provider's network status is, let me see...", id="unfinished-speech"),
    pytest.param("Please continue to hold. Your representative will be with you shortly.", id="hold-announcement"),
])
async def test_yields_when_no_response_is_needed(session, start_assistant, llm, update):
    agent = await start_assistant()
    await replay(agent, LOOKUP)

    result = await session.run(user_input=update)
    show_response(update, result)

    await assert_yields_turn(result, llm, after=update)
    assert session.current_agent is agent


@pytest.mark.parametrize("question, intent", [
    pytest.param("Are you still there?", "Briefly confirms that the caller is still on the line.",
                 id="presence-check"),
    pytest.param("I'm still checking, but can you confirm the member ID?",
                 "Provides the member ID W123456789, possibly spelling letters and digits as words.",
                 id="information-request-during-lookup"),
    pytest.param("Can I place you on hold a little longer?",
                 "Briefly agrees to remain on hold.", id="hold-permission"),
    pytest.param("Thanks for holding. The provider is in network. What benefits do you need?",
                 "Requests physical therapy benefits rather than asking the network status again.",
                 id="lookup-complete"),
])
async def test_answers_when_the_representative_needs_a_response(
    session, start_assistant, llm, question, intent,
):
    agent = await start_assistant()
    await replay(agent, LOOKUP)

    result = await session.run(user_input=question)
    show_response(question, result)

    assert not called(result, "end_call")
    assert_wait_is_exclusive(result)
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=f"In response to the representative saying {question!r}: {intent} "
        "A concise answer may rely on that context; it need not restate the question.",
    )
