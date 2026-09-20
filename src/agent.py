import asyncio
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
    inference,
    llm,
    room_io,
    stt,
    tts,
)
from livekit.plugins import noise_cancellation, silero

import call_reference
import interview
import navigator
import prompts
import report
from dispatch import load_sample_spec, parse_dispatch
from navigator import NavigatorAgent
from recording import start_recording
from report import on_session_end as save_session_report
from tracing import setup_langfuse
from voices import tts_chain

logger = logging.getLogger(__name__)

# The model order is a decision, so it lives in one place. Tests import these
# rather than restating them, so they always run against what production runs.
PRIMARY_LLM = "openai/gpt-4.1"
FALLBACK_LLM = "google/gemma-4-31b-it"

# Identity of the payer's SIP leg in the room. Fixed so the dial and the session
# name the same participant.
PAYER_IDENTITY = "payer"
RINGING_TIMEOUT_S = 45


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
    trace_provider = setup_langfuse(session_id=ctx.room.name)
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
        # GPT-4.1 is primary to reduce spoken stage directions; historical results
        # are in docs/decisions.md#model-selection.
        llm=llm.FallbackAdapter(
            [
                inference.LLM(model=PRIMARY_LLM),
                inference.LLM(model=FALLBACK_LLM),
            ]
        ),
        # STT with fallback: Flux handles English conversation; AssemblyAI provides
        # a second provider if it fails. Selection history: docs/decisions.md.
        stt=stt.FallbackAdapter(
            [
                inference.STT.from_model_string("deepgram/flux-general-en"),
                inference.STT.from_model_string("assemblyai/universal-streaming:en"),
            ]
        ),
        # TTS with fallback: ElevenLabs primary, then xAI, then Cartesia. See
        # tts_chain for why the primary is built there and not inline.
        tts=tts.FallbackAdapter(tts_chain(spec.tts_voice_id)),
        # Bias identity names so transcription errors do not derail verification.
        # Exclude facility names: their common words can bias unrelated speech.
        # Session ownership preserves these hints across STT fallback and handoff.
        stt_context_options=STTContextOptions(
            keyterms=[spec.member_name, spec.caller_first_name],
        ),
        # Use the audio turn detector's minimum supported silence window so VAD
        # does not impose extra latency. Timing evidence: docs/decisions.md.
        vad=silero.VAD.load(min_silence_duration=0.25),  # Voice activity detection
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),  # Audio end-of-turn detection
            endpointing={
                # Only max_delay is set; min_delay stays at the audio detector's 0.3
                # default. Cap uncertain turns while allowing mid-sentence pauses.
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

    # Start the voice pipeline before dialing to capture the payer's first audio.
    # The navigator listens first and hands off only once a human speaks.
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
                    # Match noise cancellation to SIP audio. Comparative benefit
                    # is unverified; see docs/decisions.md#telephony-noise-cancellation.
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
        allowed = {
            value for name, value in vars(api.ServerErrorCode).items() if name.isupper()
        }
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
