"""PHI remains out of stdout and unapproved telemetry destinations."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from livekit import api

import agent
import tracing


@pytest.mark.parametrize(
    "destination",
    [
        "https://cloud.langfuse.com",
        "https://hipaa.cloud.langfuse.com.evil.test",
        "http://hipaa.cloud.langfuse.com",
        "https://hipaa.cloud.langfuse.com/path",
    ],
)
def test_unapproved_trace_destination_never_constructs_exporter(
    monkeypatch, destination, caplog
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", destination)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "synthetic-secret")
    provider = Mock()
    monkeypatch.setattr(tracing, "TracerProvider", provider)
    assert tracing.setup_langfuse(session_id="vc-synthetic-call") is None
    provider.assert_not_called()
    assert destination not in caplog.text
    assert "synthetic-secret" not in caplog.text


async def test_dial_error_logs_and_shutdown_omit_provider_details(monkeypatch, caplog):
    monkeypatch.setenv("SIP_TRUNK_ID", "synthetic-trunk")
    error = api.ServerError("unavailable", "synthetic patient and phone", status=503)
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="synthetic-call"),
        shutdown=Mock(),
        api=SimpleNamespace(
            sip=SimpleNamespace(create_sip_participant=AsyncMock(side_effect=error))
        ),
    )
    assert await agent.dial(ctx, "+12025550100") is False
    assert "synthetic patient" not in caplog.text
    assert "synthetic patient" not in str(ctx.shutdown.call_args)


async def test_session_start_log_omits_caller_and_facility(monkeypatch, caplog):
    # Stop immediately at pipeline construction, before model/voice access.
    import logging

    class PipelineReached(Exception):
        pass

    monkeypatch.setattr(agent, "setup_langfuse", lambda **kwargs: None)
    monkeypatch.setattr(agent.llm, "FallbackAdapter", Mock(side_effect=PipelineReached))
    monkeypatch.setattr(agent.inference, "LLM", Mock())
    ctx = SimpleNamespace(
        is_fake_job=lambda: True,
        job=SimpleNamespace(metadata=""),
        room=SimpleNamespace(name="synthetic-call"),
    )
    spec = agent.load_sample_spec()
    with caplog.at_level(logging.INFO), pytest.raises(PipelineReached):
        await agent.entrypoint(ctx)
    assert caplog.records
    assert spec.caller_first_name not in caplog.text
    assert spec.facility_name not in caplog.text


def test_approved_exporter_pins_destination_over_ambient_otel(monkeypatch):
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporter_module
    import opentelemetry.sdk.trace.export as processor_module

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "synthetic-secret")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://unapproved.example/traces"
    )
    exporter = Mock()
    monkeypatch.setattr(exporter_module, "OTLPSpanExporter", exporter)
    monkeypatch.setattr(processor_module, "BatchSpanProcessor", Mock())
    monkeypatch.setattr(tracing, "TracerProvider", Mock())
    monkeypatch.setattr(tracing, "set_tracer_provider", Mock())
    assert tracing.setup_langfuse(session_id="vc-synthetic-call") is not None
    assert (
        exporter.call_args.kwargs["endpoint"]
        == "https://hipaa.cloud.langfuse.com/api/public/otel/v1/traces"
    )
    assert exporter.call_args.kwargs["headers"]["x-langfuse-ingestion-version"] == "4"


@pytest.mark.parametrize(
    ("code", "sip", "expected"),
    [
        ("unavailable", "486", "sip_486"),
        ("not_found", None, "not_found"),
        ("synthetic patient secret", None, "unknown"),
        ("unavailable", "12025550100", "unavailable"),
    ],
)
async def test_dial_retains_only_validated_protocol_codes(
    monkeypatch, caplog, code, sip, expected
):
    monkeypatch.setenv("SIP_TRUNK_ID", "synthetic-trunk")
    error = api.SipCallError(
        code,
        "synthetic patient secret",
        status=503,
        metadata={"sip_status_code": sip} if sip else {},
    )
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="synthetic-call"),
        shutdown=Mock(),
        api=SimpleNamespace(
            sip=SimpleNamespace(create_sip_participant=AsyncMock(side_effect=error))
        ),
    )
    assert not await agent.dial(ctx, "+12025550100")
    assert ctx.shutdown.call_args.kwargs["reason"] == f"dial failed: {expected}"
    assert expected in caplog.text
    assert "synthetic patient secret" not in caplog.text
    assert "12025550100" not in caplog.text
    # A dial that never connected must not be stamped as answered.
    assert not hasattr(ctx, "answered_at")


async def test_answered_dial_marks_the_answer_time(monkeypatch):
    monkeypatch.setenv("SIP_TRUNK_ID", "synthetic-trunk")
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="synthetic-call"),
        shutdown=Mock(),
        api=SimpleNamespace(sip=SimpleNamespace(create_sip_participant=AsyncMock())),
    )
    before = time.time()
    assert await agent.dial(ctx, "+12025550100") is True
    assert before <= ctx.answered_at <= time.time()
    ctx.shutdown.assert_not_called()


@pytest.fixture
def restore_livekit_tracer():
    """Undo ``set_tracer_provider``: it rebinds a module-global tracer, which
    monkeypatch cannot see, and the provider it installs must be shut down or its
    processors keep collecting every later test's spans."""
    from livekit.agents.telemetry import tracer as lk_tracer

    previous = lk_tracer._tracer_provider
    yield
    installed = lk_tracer._tracer_provider
    lk_tracer.set_provider(previous)
    if installed is not previous and hasattr(installed, "shutdown"):
        installed.shutdown()


def test_spans_carry_call_id_as_langfuse_session_inside_a_job(
    monkeypatch, restore_livekit_tracer
):
    """Langfuse groups a call by ``langfuse.session.id``. LiveKit's own metadata
    processor only applies caller metadata outside a job context; inside one it
    stamps its job attributes and returns, so Langfuse fell back to the room SID.

    The span is created through LiveKit's tracer, the accessor the voice pipeline
    uses, so the test also guards the wiring from ``set_tracer_provider`` to the
    spans that actually reach Langfuse."""
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporter_module
    import opentelemetry.sdk.trace.export as processor_module
    from livekit.agents.telemetry import tracer as lk_tracer
    from livekit.agents.telemetry import traces as lk_traces
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "synthetic-secret")
    monkeypatch.setattr(exporter_module, "OTLPSpanExporter", Mock())
    monkeypatch.setattr(processor_module, "BatchSpanProcessor", Mock())
    # Simulate a running job the way livekit-agents 1.8.0 resolves it: the metadata
    # processor asks this private hook for per-job attributes and, when it answers,
    # stamps those and returns. If LiveKit renames it, this is a LiveKit change to
    # re-verify, not a regression in the agent.
    assert hasattr(lk_traces, "_job_stamp_attributes"), (
        "livekit-agents changed its job telemetry internals"
    )
    monkeypatch.setattr(
        lk_traces, "_job_stamp_attributes", lambda: {"room_id": "RM_synthetic"}
    )

    monkeypatch.setenv("TRACING_SERVICE_NAME", "synthetic-worker")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ambient-service")
    provider = tracing.setup_langfuse(session_id="vc-synthetic-call")
    assert provider is not None
    captured = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(captured))
    with lk_tracer.start_as_current_span("llm_request"):
        pass
    [span] = captured.get_finished_spans()
    assert span.attributes["langfuse.session.id"] == "vc-synthetic-call"
    assert span.attributes["room_id"] == "RM_synthetic"
    assert span.resource.attributes["service.name"] == "synthetic-worker"
