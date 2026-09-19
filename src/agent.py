import asyncio
import base64
import logging
from os import getenv

from dotenv import load_dotenv
from google.protobuf import duration_pb2
from livekit import agents, api
from livekit.agents import (
    NOT_GIVEN,
    AgentServer,
    AgentSession,
    JobContext,
    STTContextOptions,
    TurnHandlingOptions,
    room_io,
)
from livekit.plugins import elevenlabs, noise_cancellation, silero
from livekit.agents import llm, stt, tts, inference
from livekit.agents.telemetry import set_tracer_provider
from opentelemetry import context as otel_context
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

import call_reference
import interview
import navigator
import prompts
from navigator import NavigatorAgent
from dispatch import load_sample_spec, parse_dispatch
import report
from report import on_session_end as save_session_report
from recording import start_recording


logger = logging.getLogger(__name__)

# The model order is a decision, so it lives in one place. Tests import these
# rather than restating them, so they always run against what production runs.
PRIMARY_LLM = "openai/gpt-4.1"
FALLBACK_LLM = "google/gemma-4-31b-it"

# Identity of the payer's SIP leg in the room. Fixed so the dial and the session
# name the same participant.
PAYER_IDENTITY = "payer"
RINGING_TIMEOUT_S = 45


class _SessionIdSpanProcessor(SpanProcessor):
    """Stamp ``langfuse.session.id`` on every span so Langfuse groups the call by it.

    ``set_tracer_provider(metadata=...)`` cannot do this: inside a job LiveKit's own
    metadata processor stamps its job attributes and returns without applying the
    caller's metadata, so Langfuse fell back to ``gen_ai.conversation.id`` -- the
    room SID -- and the call was unsearchable by its ``vc-`` id.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def on_start(self, span: Span, parent_context: otel_context.Context | None = None) -> None:
        span.set_attribute("langfuse.session.id", self._session_id)


def _setup_langfuse(session_id: str) -> TracerProvider | None:
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
        logger.warning("Tracing disabled: destination is not approved for clinical data")
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
    provider = TracerProvider(
        sampler=ALWAYS_ON, resource=Resource({SERVICE_NAME: TRACING_SERVICE_NAME})
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


# override=True so this project's config file wins over ambient shell exports.
# Without it, a LANGFUSE_* (or LIVEKIT_*) variable exported in your profile for
# another project silently takes precedence and this agent's traces go there.
load_dotenv(override=True)

# The name this worker registers under with LiveKit, and the name the backend
# dispatches to. Set it per deployment (a deployed worker reads it from its
# secrets); the default is only for local runs. Renaming a deployed worker needs
# a coordinated redeploy plus a backend setting change — a mismatch means
# dispatches no worker answers.
VOICE_CALL_AGENT_NAME = getenv("VOICE_CALL_AGENT_NAME", "voice-agent").strip()
if not VOICE_CALL_AGENT_NAME:
    raise RuntimeError("VOICE_CALL_AGENT_NAME must not be blank for the worker")
# The OTel service.name stamped on every trace. Read here, not from
# OTEL_SERVICE_NAME, for the same reason _setup_langfuse ignores ambient OTEL_*
# settings: nothing outside this file may relabel or redirect clinical spans.
TRACING_SERVICE_NAME = getenv("TRACING_SERVICE_NAME", "voice-agent").strip() or "voice-agent"

# Give report serialization and the S3 SDK retries their own session-end budget.
server = AgentServer(session_end_timeout=60, shutdown_process_timeout=60)


async def on_session_end(ctx: JobContext) -> None:
    # Job shutdown disconnects the agent, but can leave the SIP caller connected.
    # End the dedicated call room before spending time uploading its report.
    if not ctx.is_fake_job():
        try:
            async with asyncio.timeout(10):
                await ctx.delete_room()
        except Exception:
            logger.error("Call room cleanup failed; continuing with session report")
    await save_session_report(ctx)


# The voice Jorge picked by ear for the ElevenLabs switch.
DEFAULT_TTS_VOICE_ID = "QTKSa2Iyv0yoxvXY2V8a"
# eleven_flash_v2_5 is ElevenLabs' low-latency model (~75ms), on the standard
# streaming websocket. It does not normalize numbers on its own, so the prompt
# spells every identifier out as words (prompts/verification.md, SPEECH DELIVERY).
# eleven_v3_conversational was tried first and dropped: it runs on the newer
# text-to-dialogue path and did not hold up on calls.
TTS_MODEL = "eleven_flash_v2_5"

# Pinned, not inherited. Without these the plugin opens the websocket with an
# empty voice_settings object, and whether ElevenLabs then applies the voice's
# stored settings or its platform defaults is undocumented; the playground with
# settings unset applies the stored ones, so the two never sounded alike. These
# are the stored settings for the default voice with stability raised from
# 0.55: the read on calls came out excited on short sentences ("Certainly."),
# and stability is ElevenLabs' lever for that. Set the same values in the
# playground to reproduce a call.
TTS_VOICE_SETTINGS = elevenlabs.VoiceSettings(
    stability=0.7, similarity_boost=0.4, style=0.0, speed=1.02, use_speaker_boost=True
)


def _tts_chain(voice_id: str | None = None) -> list[tts.TTS]:
    """The TTS fallback chain, primary first.

    ElevenLabs left LiveKit Inference, so it runs on our own account and its
    constructor raises when ELEVEN_API_KEY is missing. That raise lands inside
    the job, before the session exists: it would fail every dispatched call
    without a word spoken, and the two fallbacks behind it -- the reason this is
    a chain at all -- would never be reached. A missing key drops the voice and
    says so in the log instead.

    ElevenLabs is not covered by LiveKit's HIPAA BAA; the two behind it are, on
    other vendors.
    """
    chain: list[tts.TTS] = []
    if getenv("ELEVEN_API_KEY"):
        chain.append(
            elevenlabs.TTS(
                model=TTS_MODEL,
                voice_id=voice_id or DEFAULT_TTS_VOICE_ID,
                voice_settings=TTS_VOICE_SETTINGS,
            )
        )
    else:
        logger.error("ELEVEN_API_KEY is not set; the call runs on the xAI voice instead")
    chain.append(inference.TTS.from_model_string("xai/tts-1:carina"))
    chain.append(
        inference.TTS.from_model_string("cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc")
    )
    return chain


# The entrypoint function runs when a participant joins the room
@server.rtc_session(agent_name=VOICE_CALL_AGENT_NAME, on_session_end=on_session_end)
async def entrypoint(ctx: JobContext):
    if ctx.is_fake_job() and not ctx.job.metadata:
        # Only the SDK's local console job can use synthetic call data.
        spec = load_sample_spec()
        payer_phone = None
    else:
        spec = parse_dispatch(ctx.job.metadata)
        payer_phone = spec.payer_phone_number

    # Validate both phases before dialing, not minutes later at the handoff.
    prompts.instructions(navigator.PROMPT, spec)
    prompts.instructions(interview.PROMPT, spec)
    prompts.instructions(call_reference.PROMPT, spec)

    # Set up before the session starts, so the pipeline's spans are routed. The room
    # name doubles as the Langfuse session id, grouping every span from this call.
    # Production rooms are the call id (vc-<uuid>); the Colab notebook dials ttfa-*.
    trace_provider = _setup_langfuse(session_id=ctx.room.name)
    if trace_provider is not None:

        async def flush_traces():
            # Spans are batched, so without this the tail of a call is lost when the
            # job process exits. force_flush is a blocking network send (up to 30s),
            # so it runs in a thread and the room teardown keeps its own timeout.
            await asyncio.to_thread(trace_provider.force_flush)

        ctx.add_shutdown_callback(flush_traces)

    logger.info("Starting call session")

    # Configure the voice pipeline with STT, LLM, TTS, and VAD providers
    session: AgentSession = AgentSession(
        # LLM with fallback: GPT-4.1 primary, Gemma backup.
        # Fallback fires on provider errors only -- timeouts, 4xx/5xx, mid-stream
        # drops -- never on output quality, so the primary handles every call that
        # doesn't error.
        #
        # Order set from evidence, not preference: on the seeded context from call
        # ttfa-20260905-000505, Gemma emitted a spoken stage direction in 6 of 10
        # runs and GPT-4.1 in 0 of 10. See test/interview/test_stage_directions.py.
        llm=llm.FallbackAdapter(
            [
                inference.LLM(model=PRIMARY_LLM),
                inference.LLM(model=FALLBACK_LLM),
            ]
        ),
        # STT with fallback: Deepgram Flux primary, AssemblyAI backup. Flux is
        # Deepgram's model built for voice-agent conversation (English), replacing
        # Nova-3 as a trial.
        stt=stt.FallbackAdapter(
            [
                inference.STT.from_model_string("deepgram/flux-general-en"),
                inference.STT.from_model_string("assemblyai/universal-streaming:en"),
            ]
        ),
        # TTS with fallback: ElevenLabs primary, then xAI, then Cartesia. See
        # _tts_chain for why the primary is built there and not inline.
        tts=tts.FallbackAdapter(_tts_chain(spec.tts_voice_id)),
        # Words the recognizer would otherwise guess at. On a live call the menu
        # read back the patient "Rashid" and STT heard "Richard Amari", so the
        # navigator denied its own authentication. Session-owned, so it survives
        # the handoff, and mapped into whichever STT is active.
        #
        # The member's name and our own caller name, which reps repeat back all call
        # ("Thank you, Greta", "Hi Greta") and STT mangles: on one live call it wrote
        # "Praia", "Maria", "prayer" and "Pete" for the caller name of the day.
        #
        # The facility name was a keyterm too, until a live call: for a facility
        # named "<something> ABA LLC" the rep's "Hold on" came back as "ABA." (confidence 0.57)
        # right after the agent said "ABA therapy". Facility names are full of words a
        # rep says for other reasons; a first name is only ever the caller.
        stt_context_options=STTContextOptions(
            keyterms=[spec.member_name, spec.caller_first_name],
        ),
        # 0.25s is the floor the audio turn detector allows (lower raises ValueError
        # at session start). The default 0.55 was setting the wait on every turn the
        # detector cleared: measured 0.577s each time, on every session.
        vad=silero.VAD.load(min_silence_duration=0.25),  # Voice activity detection
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),  # Audio end-of-turn detection
            endpointing={
                # Only max_delay is set; min_delay stays at the audio detector's 0.3
                # default, which now binds the floor instead of the VAD's silence window.
                #
                # 2.5 (the default) is what every turn scoring under the 0.56 threshold
                # waited -- about half of them on real calls. 1.5 still clears the
                # longest genuine mid-turn pause measured so far (1.164s) by 0.34s.
                "max_delay": 1.5,
            },
            preemptive_generation={
                "enabled": True,  # Start the LLM before the turn is confirmed
                "preemptive_tts": True,  # Synthesize during the endpointing wait, not after it
            },
        ),
    )

    # Per-turn latency and token usage are not logged here: every span LiveKit
    # exports to Langfuse already carries them (lk.end_of_turn_delay,
    # lk.response.ttft, lk.response.ttfb, lk.llm_metrics, lk.tts_metrics).

    # Start the session with noise cancellation enabled.
    #
    # BVCTelephony is the telephony-tuned Krisp model, for narrowband (8 kHz) SIP
    # audio. Plain BVC is tuned for wideband and was what ran during the phone
    # benchmark, where the turn detector cleared only 50% of turns. Swapped here to
    # measure whether matching the model to the audio raises that rate.
    #
    # NOTE: this makes the agent telephony-specific. For a mixed deployment, pick
    # the model from the participant kind (BVC for WebRTC, BVCTelephony for SIP).
    #
    # Start the session before dialing, so the room connect, the track
    # subscription and the STT stream are all open by the time the line is
    # answered -- a payer's menu starts talking on the first frame, and this
    # agent never speaks first, so there is no greeting to hold back. (The
    # outbound-calls guide starts after the answer only to keep an opening
    # greeting from playing into the ringing; LiveKit's own outbound example
    # starts first for the same reason as here.) Whatever answers (a menu, a
    # person, a voicemail) is the navigator's to judge: it hands off to the
    # interview when a human speaks, and hangs up on a voicemail greeting.
    #
    # The call starts with the navigator; the InterviewAgent is only ever reached by
    # its handoff, once a human is on the line.
    if payer_phone:
        # Egress requires an existing room. Request it before placing the SIP call.
        await ctx.connect()
        await start_recording(ctx)

    started = asyncio.create_task(
        session.start(
            agent=NavigatorAgent(spec=spec),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                # Link the session to the payer's leg by name, not to whoever joins
                # first. Console mode has no phone and takes the default.
                participant_identity=PAYER_IDENTITY if payer_phone else NOT_GIVEN,
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=noise_cancellation.BVCTelephony(),
                ),
            ),
        )
    )
    if not payer_phone:
        await started
        return

    if not await dial(ctx, payer_phone):
        # session.start registered its own close on the job's shutdown, so the
        # session is torn down with the job; no need to cancel it mid-start.
        return
    await started
    # RoomIO links the leg on join and closes the session if it later drops.
    # The one gap is a leg that answered and left before the link, which
    # nothing would ever notice: the room stays up with only the agent in it.
    if PAYER_IDENTITY not in ctx.room.remote_participants:
        logger.error("Payer answered but left the room before the session linked")
        ctx.shutdown(reason="payer left before the session linked")


async def dial(ctx: JobContext, payer_phone: str) -> bool:
    """Place the call. True once the payer has answered.

    Voicemail is not a failure here: a mailbox answers the call like anyone
    else. Busy, no answer and rejected surface as SipCallError; a bad trunk,
    a malformed number or an auth failure as a plain ServerError; an API that
    never responds as TimeoutError. For all of those the job releases itself.
    """
    trunk = getenv("SIP_TRUNK_ID")
    if not trunk:
        raise RuntimeError("SIP_TRUNK_ID is not set; the agent cannot dial")

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk,
                sip_call_to=payer_phone,
                room_name=ctx.room.name,
                participant_identity=PAYER_IDENTITY,
                wait_until_answered=True,
                # Give up on an unanswered line before the carrier's own limit.
                ringing_timeout=duration_pb2.Duration(seconds=RINGING_TIMEOUT_S),
            )
        )
    except api.ServerError as exc:
        # Only documented protocol codes; never log the provider's message/metadata.
        # https://docs.livekit.io/reference/python/livekit/api/twirp_client.html
        sip = exc.sip_status_code if isinstance(exc, api.SipCallError) else None
        allowed = {value for name, value in vars(api.ServerErrorCode).items() if name.isupper()}
        code = exc.code if exc.code in allowed else "unknown"
        if sip is not None and 100 <= sip <= 699:
            code = f"sip_{sip}"
        logger.error("Dial failed: %s", code)
        ctx.shutdown(reason=f"dial failed: {code}")
        return False
    except TimeoutError:
        # The client's own timeout is ringing_timeout plus a margin, so this is
        # the API not answering, not the line.
        logger.error("Dial timed out waiting on the API")
        ctx.shutdown(reason="dial failed: request timeout")
        return False
    # wait_until_answered returned: the payer is on the line as of now.
    report.mark_answered(ctx)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
