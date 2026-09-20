from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from livekit import api
from livekit.agents import JobContext

import agent


@pytest.fixture
def call_context():
    # Exercise the installed SDK's room deletion helper at its network boundary.
    ctx = SimpleNamespace(
        is_fake_job=lambda: False,
        _room=SimpleNamespace(name="synthetic-call"),
        api=SimpleNamespace(room=SimpleNamespace(delete_room=AsyncMock())),
        _track_pending_task=lambda task, **kwargs: None,
    )
    ctx.room = ctx._room
    ctx.delete_room = lambda: JobContext.delete_room(ctx)
    return ctx


async def test_session_end_deletes_room_before_report(call_context, monkeypatch):
    ctx = call_context

    async def save_report(context):
        assert context is ctx
        ctx.api.room.delete_room.assert_awaited_once()

    save = AsyncMock(side_effect=save_report)
    monkeypatch.setattr(agent, "save_session_report", save)
    await agent.on_session_end(ctx)
    request = ctx.api.room.delete_room.call_args.args[0]
    assert request.room == "synthetic-call"
    save.assert_awaited_once_with(ctx)


async def test_console_does_not_delete_room(call_context, monkeypatch):
    ctx = call_context
    ctx.is_fake_job = lambda: True
    monkeypatch.setattr(agent, "save_session_report", AsyncMock())
    await agent.on_session_end(ctx)
    ctx.api.room.delete_room.assert_not_awaited()


async def test_cleanup_failure_does_not_block_report(call_context, monkeypatch, caplog):
    ctx = call_context
    ctx.delete_room = AsyncMock(side_effect=TimeoutError("sensitive details"))
    save = AsyncMock()
    monkeypatch.setattr(agent, "save_session_report", save)
    await agent.on_session_end(ctx)
    save.assert_awaited_once_with(ctx)
    assert "sensitive" not in caplog.text
    assert caplog.records


async def test_dial_timeout_runs_cleanup_with_session_end(call_context, monkeypatch):
    ctx = call_context
    monkeypatch.setenv("SIP_TRUNK_ID", "synthetic-trunk")
    ctx.api.sip = SimpleNamespace(
        create_sip_participant=AsyncMock(side_effect=TimeoutError())
    )
    reasons = []
    ctx.shutdown = lambda reason: reasons.append(reason)
    monkeypatch.setattr(agent, "save_session_report", AsyncMock())
    assert not await agent.dial(ctx, "+12025550100")
    assert reasons == ["dial failed: request timeout"]
    # The SDK invokes this hook after the requested shutdown.
    await agent.on_session_end(ctx)
    ctx.api.room.delete_room.assert_awaited_once()


async def test_already_deleted_room_still_saves_report(call_context, monkeypatch):
    ctx = call_context
    ctx.api.room.delete_room.side_effect = api.TwirpError(
        api.TwirpErrorCode.NOT_FOUND,
        "room already deleted",
        status=404,
    )
    save = AsyncMock()
    monkeypatch.setattr(agent, "save_session_report", save)
    await agent.on_session_end(ctx)
    save.assert_awaited_once_with(ctx)
