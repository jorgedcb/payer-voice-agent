import pytest


@pytest.mark.asyncio
async def test_assistant_greeting(session, start_assistant, llm) -> None:
    await start_assistant(caller_first_name="Annie", caller_last_initial="S")

    result = await session.run(
        user_input="Good afternoon. Thank you for calling ATMA. This is Alice I'll be your customer advocate for today. How may I help you?"
    )

    await (
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            llm,
            intent=(
                "Says it is calling about benefits information for one of the plan's "
                "members, in one or two short sentences. It may give its name. It does "
                "not offer assistance or ask how it can help, and does not read out any "
                "member or provider details."
            ),
        )
    )

    result.expect.no_more_events()
