"""Clinical tracing configuration and per-call span attribution."""

import base64
import logging
from os import getenv

from livekit.agents.telemetry import set_tracer_provider
from opentelemetry import context as otel_context
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

logger = logging.getLogger(__name__)


class _SessionIdSpanProcessor(SpanProcessor):
    """Stamp ``langfuse.session.id`` on every span so Langfuse groups the call by it.

    ``set_tracer_provider(metadata=...)`` cannot do this: inside a job LiveKit's own
    metadata processor stamps its job attributes and returns without applying the
    caller's metadata, so Langfuse fell back to ``gen_ai.conversation.id`` -- the
    room SID -- and the call was unsearchable by its ``vc-`` id.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def on_start(
        self, span: Span, parent_context: otel_context.Context | None = None
    ) -> None:
        span.set_attribute("langfuse.session.id", self._session_id)


def setup_langfuse(session_id: str) -> TracerProvider | None:
    """Route this session's OpenTelemetry spans to Langfuse.

    ``session_id`` (the room name) becomes the Langfuse session for every span, so
    a call can be found by its ``vc-`` id. Returns None when the keys aren't
    configured, so console mode and local runs behave exactly as before -- tracing
    stays inert until the secrets exist.

    Note what these spans carry: lk.pii.instructions is the fully rendered prompt
    (member name, DOB, member ID, NPI) and lk.pii.user_transcript is everything
    the representative said. LiveKit's PII redaction covers its own storage, not
    traces exported from here. Only the approved HIPAA destination is accepted;
    the operator must keep its BAA in force before enabling the keys.
    """
    public_key = getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = getenv("LANGFUSE_SECRET_KEY")
    base_url = getenv("LANGFUSE_BASE_URL")
    if not (public_key and secret_key and base_url):
        logger.info("Langfuse not configured; tracing disabled for this session")
        return None

    if base_url.rstrip("/") != "https://hipaa.cloud.langfuse.com":
        logger.warning(
            "Tracing disabled: destination is not approved for clinical data"
        )
        return None

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    # Explicit arguments prevent ambient OTEL trace settings from redirecting PHI.
    exporter = OTLPSpanExporter(
        endpoint="https://hipaa.cloud.langfuse.com/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
    )
    if not session_id:
        logger.warning("Tracing session id is empty; spans will not group by call")
    # Explicit sampler and resource for the same reason as the exporter: ambient
    # OTEL_TRACES_SAMPLER / OTEL_RESOURCE_ATTRIBUTES must not silently drop or
    # relabel clinical spans. Resource() -- not Resource.create() -- reads no env.
    # Read after the entrypoint has loaded this deployment's configuration.
    # Ignore OTEL_SERVICE_NAME so ambient settings cannot relabel clinical spans.
    service_name = (
        getenv("TRACING_SERVICE_NAME", "voice-agent").strip() or "voice-agent"
    )
    provider = TracerProvider(
        sampler=ALWAYS_ON, resource=Resource({SERVICE_NAME: service_name})
    )
    if session_id:
        provider.add_span_processor(_SessionIdSpanProcessor(session_id))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # Any non-empty metadata makes LiveKit install its own processor, which stamps
    # room_id, job_id and agent name on every span inside a job. Outside a job it
    # stamps this dict instead, so the session id is the right fallback content.
    set_tracer_provider(provider, metadata={"langfuse.session.id": session_id})
    logger.info("Clinical tracing enabled at approved destination")
    return provider
