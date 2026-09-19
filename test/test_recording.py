from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from livekit import api

from recording import start_recording


@pytest.fixture
def recording_ctx(monkeypatch):
    for key, value in {
        "AUDIO_RECORDING_BUCKET": "synthetic-recordings",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "synthetic-key",
        "AWS_SECRET_ACCESS_KEY": "synthetic-secret",
        "AWS_SESSION_TOKEN": "synthetic-token",
    }.items():
        monkeypatch.setenv(key, value)
    egress = SimpleNamespace(
        start_room_composite_egress=AsyncMock(
            return_value=api.EgressInfo(egress_id="EG_test")
        ),
        stop_egress=AsyncMock(
            return_value=api.EgressInfo(
                egress_id="EG_test", status=api.EgressStatus.EGRESS_COMPLETE
            )
        ),
        list_egress=AsyncMock(),
    )
    callbacks = []
    return SimpleNamespace(
        is_fake_job=lambda: False,
        room=SimpleNamespace(name="call-123"),
        api=SimpleNamespace(egress=egress),
        add_shutdown_callback=callbacks.append,
        callbacks=callbacks,
    )


async def test_audio_request_and_shutdown(recording_ctx):
    ctx = recording_ctx
    await start_recording(ctx)
    request = ctx.api.egress.start_room_composite_egress.call_args.args[0]
    assert request.room_name == "call-123"
    assert request.audio_only
    (output,) = request.file_outputs
    assert output.file_type == api.EncodedFileType.OGG
    assert output.filepath == "calls/call-123/audio.ogg"
    assert output.s3.bucket == "synthetic-recordings"
    assert output.s3.session_token == "synthetic-token"
    await ctx.callbacks[0]()
    assert ctx.api.egress.stop_egress.call_args.args[0].egress_id == "EG_test"


async def test_shutdown_leaves_upload_completion_to_the_backend(
    recording_ctx, monkeypatch
):
    ctx = recording_ctx
    ctx.api.egress.stop_egress.return_value = api.EgressInfo(
        status=api.EgressStatus.EGRESS_ENDING
    )
    ctx.api.egress.list_egress.return_value = api.ListEgressResponse(
        items=[api.EgressInfo(status=api.EgressStatus.EGRESS_COMPLETE)]
    )
    monkeypatch.setattr("recording.asyncio.sleep", AsyncMock())
    await start_recording(ctx)
    await ctx.callbacks[0]()
    ctx.api.egress.stop_egress.assert_awaited_once()
    ctx.api.egress.list_egress.assert_not_awaited()


@pytest.mark.parametrize("phase", ["start", "stop"])
async def test_recording_errors_are_contained(recording_ctx, caplog, phase):
    ctx = recording_ctx
    if phase == "start":
        ctx.api.egress.start_room_composite_egress.side_effect = RuntimeError(
            "sensitive details"
        )
    else:
        ctx.api.egress.stop_egress.side_effect = RuntimeError("sensitive details")
    await start_recording(ctx)
    for callback in ctx.callbacks:
        await callback()
    assert "recording" in caplog.text.lower()
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("skip", ["console", "bucket", "credentials"])
async def test_skips_unconfigured_recording(recording_ctx, monkeypatch, skip):
    ctx = recording_ctx
    if skip == "console":
        ctx.is_fake_job = lambda: True
    elif skip == "bucket":
        monkeypatch.delenv("AUDIO_RECORDING_BUCKET")
    else:
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    await start_recording(ctx)
    ctx.api.egress.start_room_composite_egress.assert_not_awaited()
    assert not ctx.callbacks


@pytest.mark.parametrize("endpoint", ["", "https://s3.example.test/storage/v1/s3"])
async def test_recording_storage_endpoint(recording_ctx, monkeypatch, endpoint):
    monkeypatch.setenv("S3_ENDPOINT_URL", endpoint)
    await start_recording(recording_ctx)
    request = recording_ctx.api.egress.start_room_composite_egress.call_args.args[0]
    storage = request.file_outputs[0].s3
    assert storage.endpoint == endpoint
    assert storage.force_path_style == bool(endpoint)
