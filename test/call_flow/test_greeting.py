"""The opening: written by the navigator at handoff and spoken by the interview at once."""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from livekit.agents import llm
from pydantic import ValidationError

from dispatch import CallSpec
from dispatch import sample_spec as spec
from interview import InterviewAgent
from prompts import RESERVED_TEMPLATE_NAMES, instructions

# The navigator writes the opening and the interview answers identity questions
# for the rest of the call, so both prompts carry the name and spell the initial.
NAMED_PROMPTS = ["navigator", "verification"]


@pytest.mark.parametrize("prompt", NAMED_PROMPTS)
def test_prompt_gives_the_name_and_spelled_initial(prompt: str) -> None:
    text = instructions(
        prompt, spec(caller_first_name="Megan", caller_last_initial="R")
    )
    assert "Megan" in text
    assert "R as in romeo" in text


@pytest.mark.parametrize("prompt", NAMED_PROMPTS)
def test_prompt_without_last_initial_asks_for_no_initial(prompt: str) -> None:
    # The spelling is what must disappear, not the words around it: asserting on
    # the surrounding phrasing passes whether or not the initial is rendered.
    text = instructions(
        prompt, spec(caller_first_name="Megan", caller_last_initial=None)
    )
    assert "Megan" in text
    assert "romeo" not in text


@pytest.mark.parametrize("initial", ["Ñ", "Sm", "3", "."])
def test_unspellable_last_initial_is_rejected_at_dispatch(initial: str) -> None:
    # Both prompts speak the initial and the interview spells it with the NATO
    # alphabet, so anything but one Latin letter is refused before the call.
    with pytest.raises(ValidationError):
        spec(caller_last_initial=initial)


def test_blank_last_initial_means_none() -> None:
    assert spec(caller_last_initial="  ").caller_last_initial is None


def test_template_names_do_not_shadow_spec_fields() -> None:
    # instructions() splats the spec's fields next to these names; a collision
    # would raise at dial time for every call.
    assert not RESERVED_TEMPLATE_NAMES & CallSpec.model_fields.keys()


def _rep_asked_name() -> llm.ChatContext:
    ctx = llm.ChatContext()
    ctx.add_message(
        role="user", content="Thanks for calling, this is Alice. May I have your name?"
    )
    return ctx


async def test_on_enter_speaks_the_handoff_opening_without_a_model_call() -> None:
    agent = InterviewAgent(
        spec=spec(), chat_ctx=_rep_asked_name(), opening="Hi Alice, this is Greta."
    )
    session = MagicMock()
    with patch.object(
        InterviewAgent, "session", new_callable=PropertyMock, return_value=session
    ):
        await agent.on_enter()
    session.say.assert_called_once_with(
        "Hi Alice, this is Greta.", add_to_chat_ctx=True
    )
    session.generate_reply.assert_not_called()


@pytest.mark.parametrize("opening", [None, "", "   "])
async def test_on_enter_stays_silent_without_an_opening(opening) -> None:
    # Production reaches this agent only through the navigator's handoff, which
    # always writes an opening. Anything else is a direct start, where the other
    # side speaks first and the ordinary turn loop answers.
    agent = InterviewAgent(spec=spec(), chat_ctx=_rep_asked_name(), opening=opening)
    session = MagicMock()
    with patch.object(
        InterviewAgent, "session", new_callable=PropertyMock, return_value=session
    ):
        await agent.on_enter()
    session.say.assert_not_called()
    session.generate_reply.assert_not_called()


async def test_on_enter_speaks_the_opening_even_before_the_rep_is_on_record() -> None:
    # The handoff's own turn is bookkeeping in the chat context, so what the agent
    # says is decided by the opening it was given, not by what the history holds.
    agent = InterviewAgent(spec=spec(), opening="Hi, this is Greta.")
    session = MagicMock()
    with patch.object(
        InterviewAgent, "session", new_callable=PropertyMock, return_value=session
    ):
        await agent.on_enter()
    session.say.assert_called_once_with("Hi, this is Greta.", add_to_chat_ctx=True)
