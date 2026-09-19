"""The two-agent shape: navigator first, interview only after a human answers.

These tests pin the mechanics of the handoff, not menu judgment. They should keep
passing unchanged once the navigator grows real IVR logic.
"""

from support.calls import handoff_opening, show_response, turn_settings
from interview import InterviewAgent

REP_GREETING = (
    "Good afternoon, thank you for calling provider services. This is Alice. "
    "How can I help you today?"
)


async def test_rep_greeting_hands_off_to_interview(session, start_navigator, llm) -> None:
    await start_navigator(caller_first_name="Greta", caller_last_initial="S")

    result = await session.run(user_input=REP_GREETING)
    show_response(REP_GREETING, result)

    # One turn, in order: the tool is called with the opening, it returns, control
    # moves to the InterviewAgent, and the InterviewAgent speaks that opening word
    # for word, without a second model call.
    opening = handoff_opening(result)
    result.expect.next_event().is_function_call_output()
    result.expect.next_event().is_agent_handoff(new_agent_type=InterviewAgent)
    spoken = result.expect.next_event().is_message(role="assistant")
    assert spoken.event().item.text_content == opening
    await spoken.judge(
        llm,
        intent=(
            "Answers the representative's 'how can I help you' by saying it is calling "
            "about benefits for one of their members, in one or two short sentences. "
            "It does not offer assistance, ask how it can help, or read out any "
            "member or provider details."
        ),
    )
    result.expect.no_more_events()

    assert isinstance(session.current_agent, InterviewAgent)


# From a live call: the rep asked only for a name and heard the whole scripted
# introduction, then asked whether the caller was a virtual assistant.
NAME_REQUEST = (
    "Thanks so much for calling provider services line. My name is Sergio. "
    "May I know your name, please?"
)


async def test_name_request_gets_only_the_name_and_initial(
    session, start_navigator, llm
) -> None:
    await start_navigator(caller_first_name="Greta", caller_last_initial="S")

    result = await session.run(user_input=NAME_REQUEST)
    show_response(NAME_REQUEST, result)

    opening = handoff_opening(result)
    result.expect.next_event().is_function_call_output()
    result.expect.next_event().is_agent_handoff(new_agent_type=InterviewAgent)
    spoken = result.expect.next_event().is_message(role="assistant")
    assert spoken.event().item.text_content == opening
    await spoken.judge(
        llm,
        intent=(
            "Gives its first name, Greta, and its last initial S, spelled out in some "
            "form such as 's for sierra' or 'S as in Sierra', in one or two short "
            "sentences. It does not say why it is calling, does not mention benefits "
            "or a member, and does not ask a question."
        ),
    )
    result.expect.no_more_events()


# A representative who only greets has asked nothing, and nobody else will speak
# first: an opening that is a greeting back leaves the two of them waiting.
BARE_GREETING = "Thank you for holding. This is Wilson."


async def test_a_bare_greeting_still_gets_the_reason_for_the_call(
    session, start_navigator, llm
) -> None:
    await start_navigator(caller_first_name="Greta", caller_last_initial="S")

    result = await session.run(user_input=BARE_GREETING)
    show_response(BARE_GREETING, result)

    opening = handoff_opening(result)
    assert opening.strip(), "handed off with nothing to say"
    await result.expect.contains_message(role="assistant").judge(
        llm,
        intent=(
            "Answers with more than a greeting: it gives its name, or says it is "
            "calling to check benefits for one of their members, or both. A reply "
            "that is only a greeting fails, since nobody would speak next."
        ),
    )


async def test_interview_does_not_reintroduce_after_its_opening(
    session, start_navigator, llm
) -> None:
    # The opening is already in the conversation: the model must treat it as
    # said. The follow-up is a turn where re-introducing is a live option, not a
    # question that forces an answer.
    await start_navigator(caller_first_name="Greta", caller_last_initial="S")
    await session.run(user_input=REP_GREETING)
    assert isinstance(session.current_agent, InterviewAgent)

    follow_up = "Hello? Sorry, are you still there?"
    result = await session.run(user_input=follow_up)
    show_response(follow_up, result)

    await result.expect.next_event().is_message(role="assistant").judge(
        llm,
        intent=(
            "Confirms it is still on the line, in a sentence or two. Does not "
            "introduce itself again with 'Hi, this is Greta', does not spell a last "
            "initial, and does not restate that it is calling about benefits for a member."
        ),
    )
    result.expect.no_more_events()


async def test_interview_sees_what_the_rep_already_said(session, start_navigator) -> None:
    await start_navigator()

    await session.run(user_input=REP_GREETING)

    # The handoff carries the conversation, not the navigator's instructions.
    interview = session.current_agent
    texts = [
        item.text_content
        for item in interview.chat_ctx.items
        if item.type == "message" and item.role == "user"
    ]
    assert REP_GREETING in texts
    assert "get the call to a live human" not in (interview.instructions or "")


async def test_navigator_turn_settings_do_not_leak_to_interview(
    session, start_navigator
) -> None:
    # The session's default is on, as in production. The navigator turns it off
    # for itself (a menu keeps talking, so the early guess is wasted) and the
    # interview must get it back, since that is where the latency gain is real.
    await start_navigator()
    endpointing, preemptive = turn_settings(session)
    assert preemptive is False
    assert endpointing["min_delay"] == 1.5
    assert endpointing["max_delay"] == 3.0

    await session.run(user_input=REP_GREETING)

    assert isinstance(session.current_agent, InterviewAgent)
    endpointing, preemptive = turn_settings(session)
    assert preemptive is True
    assert endpointing == session.options.endpointing


async def test_menu_prompt_does_not_hand_off(session, start_navigator) -> None:
    await start_navigator()

    result = await session.run(
        user_input=(
            "Thank you for calling. For claims, press one. For benefits and "
            "eligibility, press two."
        )
    )

    for ev in result.events:
        assert ev.type != "agent_handoff", "a menu recording is not a human"
