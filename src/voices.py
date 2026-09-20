"""Voice settings and the per-call TTS fallback chain."""

import logging
from os import getenv

from livekit.agents import inference, tts
from livekit.plugins import elevenlabs

logger = logging.getLogger(__name__)


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


def tts_chain(voice_id: str | None = None) -> list[tts.TTS]:
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
        logger.error(
            "ELEVEN_API_KEY is not set; the call runs on the xAI voice instead"
        )
    chain.append(inference.TTS.from_model_string("xai/tts-1:carina"))
    chain.append(
        inference.TTS.from_model_string(
            "cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        )
    )
    return chain
