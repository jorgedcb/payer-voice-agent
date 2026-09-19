"""Successful keypad input ends the turn; failed input remains recoverable."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from livekit.agents import llm
from livekit.agents.beta.workflows.utils import DtmfEvent

from dispatch import sample_spec
from navigator import HoldAgent, NavigatorAgent
from prompts import call_date_today


@pytest.fixture(params=[NavigatorAgent, HoldAgent])
def keypad(request):
    kwargs = {"spec": sample_spec(), "llm_model": MagicMock(spec=llm.LLM)}
    if request.param is HoldAgent:
        kwargs["call_date"] = call_date_today()
    agent = request.param(**kwargs)
    return next(tool for tool in agent.tools if tool.id == "send_dtmf_events")


def call_context(publish):
    return SimpleNamespace(session=SimpleNamespace(room_io=SimpleNamespace(
        room=SimpleNamespace(local_participant=SimpleNamespace(publish_dtmf=publish))
    )))


async def test_successful_keypad_input_needs_no_model_followup(keypad):
    publish = AsyncMock()
    result = await keypad(call_context(publish), [DtmfEvent("2"), DtmfEvent("#")])
    assert publish.await_args_list == [call(code=2, digit="2"), call(code=11, digit="#")]
    assert result is None, "Successful keypad input must end without another model response."


async def test_keypad_failure_is_returned_to_the_model(keypad):
    publish = AsyncMock(side_effect=RuntimeError("synthetic publish failure"))
    result = await keypad(call_context(publish), [DtmfEvent("2"), DtmfEvent("#")])
    assert "Failed to send DTMF event: 2" in result
    assert "synthetic publish failure" in result
    publish.assert_awaited_once_with(code=2, digit="2")
