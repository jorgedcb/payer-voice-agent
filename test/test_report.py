import json
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber

from report import on_session_end


@pytest.fixture
def report_context(monkeypatch):
    monkeypatch.setenv("SESSION_REPORT_BUCKET", "synthetic-reports")
    # Deliberately includes fields the frontend doesn't need: retain the native artifact.
    payload = {
        "room": "call-123",
        "chat_history": {"items": [{"role": "user", "content": ["Sí, covered."]}]},
        "events": [{"type": "agent_handoff"}],
        "audio_recording_path": "recordings/example.ogg",
    }
    ctx = SimpleNamespace(
        is_fake_job=lambda: False,
        room=SimpleNamespace(name="call-123"),
        make_session_report=Mock(return_value=SimpleNamespace(to_dict=lambda: payload)),
    )
    return ctx, payload


@pytest.fixture
def s3_client(monkeypatch):
    # Explicit synthetic credentials prevent the SDK from discovering real ones.
    client = boto3.client(
        "s3", region_name="us-east-1",
        aws_access_key_id="synthetic", aws_secret_access_key="synthetic",
    )
    monkeypatch.setattr("report.boto3.client", lambda *args, **kwargs: client)
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()


async def test_unstamped_call_uploads_the_complete_native_json(report_context, s3_client):
    # Unstamped / unanswered: no `answered_at` or `closing` on the context,
    # so the object body is the native report with nothing of ours added.
    ctx, payload = report_context
    s3_client.add_response("put_object", {}, {
        "Bucket": "synthetic-reports",
        "Key": "calls/call-123/report.json",
        "Body": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "ContentType": "application/json",
    })
    await on_session_end(ctx)
    ctx.make_session_report.assert_called_once_with()


async def test_upload_failure_is_contained_without_logging_report(report_context, s3_client, caplog):
    ctx, _ = report_context
    s3_client.add_client_error("put_object", service_error_code="AccessDenied", service_message="sensitive error details")
    await on_session_end(ctx)
    assert "Failed to save session report" in caplog.text
    assert "sensitive" not in caplog.text
    assert "covered" not in caplog.text


async def test_report_failure_is_contained(report_context, caplog):
    ctx, _ = report_context
    ctx.make_session_report.side_effect = RuntimeError("sensitive report")
    await on_session_end(ctx)
    assert "Failed to save session report" in caplog.text
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("fake, bucket", [(True, "synthetic-reports"), (False, "")])
async def test_console_or_unconfigured_jobs_skip_report(monkeypatch, fake, bucket):
    monkeypatch.setenv("SESSION_REPORT_BUCKET", bucket)
    ctx = SimpleNamespace(is_fake_job=lambda: fake, make_session_report=Mock())
    await on_session_end(ctx)
    ctx.make_session_report.assert_not_called()


@pytest.mark.parametrize('endpoint', ['', 'https://s3.example.test/storage/v1/s3'])
def test_report_storage_endpoint(monkeypatch, endpoint):
    from report import _upload_report
    monkeypatch.setenv('S3_ENDPOINT_URL', endpoint)
    factory = Mock()
    monkeypatch.setattr('report.boto3.client', factory)
    _upload_report('synthetic-reports', 'calls/example/report.json', b'{}')
    options = factory.call_args.kwargs
    assert options['endpoint_url'] == (endpoint or None)
    assert options['config'].s3['addressing_style'] == ('path' if endpoint else 'auto')
    factory.return_value.put_object.assert_called_once_with(
        Bucket='synthetic-reports', Key='calls/example/report.json',
        Body=b'{}', ContentType='application/json',
    )


async def test_answered_call_reports_agent_answered_at(
    report_context, s3_client, monkeypatch
):
    from report import mark_answered

    ctx, payload = report_context
    monkeypatch.setattr("report.time.time", lambda: 1_757_600_000.5)
    mark_answered(ctx)
    expected = dict(payload, agent_answered_at=1_757_600_000.5)
    s3_client.add_response("put_object", {}, {
        "Bucket": "synthetic-reports",
        "Key": "calls/call-123/report.json",
        "Body": json.dumps(expected, ensure_ascii=False).encode("utf-8"),
        "ContentType": "application/json",
    })
    await on_session_end(ctx)


async def test_closed_call_reports_agent_closing(report_context, s3_client):
    # What InterviewAgent.end_call stamps after the closing task: the values the
    # representative confirmed on the line, next to the native report.
    ctx, payload = report_context
    closing = {
        "reference": "AB73921",
        "unavailable_statement": None,
        "representative_name": "Jamie R",
    }
    ctx.closing = closing
    expected = dict(payload, agent_closing=closing)
    s3_client.add_response("put_object", {}, {
        "Bucket": "synthetic-reports",
        "Key": "calls/call-123/report.json",
        "Body": json.dumps(expected, ensure_ascii=False).encode("utf-8"),
        "ContentType": "application/json",
    })
    await on_session_end(ctx)


async def test_answered_and_closed_call_reports_both_stamps(
    report_context, s3_client, monkeypatch
):
    # The live path stamps both: `mark_answered` when the SIP leg answers,
    # then `InterviewAgent.end_call` writes `ctx.closing`. Independent
    # `if`s, not `elif` — key order is the insertion order `json.dumps`
    # asserts against.
    from report import mark_answered

    ctx, payload = report_context
    monkeypatch.setattr("report.time.time", lambda: 1_757_600_000.5)
    mark_answered(ctx)
    closing = {
        "reference": "AB73921",
        "unavailable_statement": None,
        "representative_name": "Jamie R",
    }
    ctx.closing = closing
    expected = dict(
        payload, agent_answered_at=1_757_600_000.5, agent_closing=closing
    )
    s3_client.add_response("put_object", {}, {
        "Bucket": "synthetic-reports",
        "Key": "calls/call-123/report.json",
        "Body": json.dumps(expected, ensure_ascii=False).encode("utf-8"),
        "ContentType": "application/json",
    })
    await on_session_end(ctx)


def test_stamp_round_trips_on_the_sdk_job_context():
    # The stamp is an attribute set on the SDK's own type; this guards against a
    # future JobContext that forbids it (slots, a frozen model) or a hook that is
    # handed a different object.
    import time
    from livekit.agents import JobContext
    from report import mark_answered

    ctx = JobContext.__new__(JobContext)
    before = time.time()
    mark_answered(ctx)
    assert before <= getattr(ctx, "answered_at") <= time.time()
