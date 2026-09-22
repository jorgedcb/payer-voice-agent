import pytest
from support.calls import replay, show_response


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


@pytest.mark.asyncio
async def test_cpt_codes_turn_leaves_the_representative_something_to_do(
    session, start_assistant, llm
) -> None:
    """A rep who answers the opening with a bare "Yes, please." is waiting for the
    codes. The turn must give them, with grammar that fits one place of service,
    and must not end in dead air: a real call went to "Hello?" and "Are you a bot?"
    after the codes were recited and the agent simply stopped."""
    agent = await start_assistant(
        caller_first_name="Annie",
        caller_last_initial="S",
        service_type="physical_therapy",
        cpt_codes=["97110", "97112", "97116"],
        service_locations=["office"],
    )
    await replay(
        agent,
        [
            ("user", "Okay. And how can I help you for today?"),
            (
                "assistant",
                "I'm looking for information about benefits for physical therapy for "
                "in network and out of network, and I have some CPT codes to check.",
            ),
        ],
    )
    result = await session.run(user_input="Yes, please.")
    show_response("Yes, please.", result)

    spoken = result.expect.next_event().is_message(role="assistant")
    # The judge has let a turn that stops on "The place of service is office."
    # through; the dead air is the whole point, so check it directly.
    assert spoken.event().item.text_content.strip().endswith("?"), (
        "turn must end with a question for the representative"
    )
    await spoken.judge(
        llm,
        intent=(
            "Gives the CPT codes 97110, 97112 and 97116 with digits spoken as "
            "words (a spoken range such as 'nine, seven, one, one, zero to nine, "
            "seven, one, one, six' is acceptable) and says the visits are in the "
            "office, in any natural phrasing ('in the office', 'the place of service "
            "is office'), never the plural 'the places of service are office'. The "
            "closing question, if any, is "
            "a check that they have the codes down, an offer to repeat, or the "
            "first benefits question — not several of these at once."
        ),
    )
    result.expect.no_more_events()
