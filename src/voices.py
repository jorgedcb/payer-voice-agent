"""Voice settings and the per-call TTS fallback chain."""

import logging
from os import getenv

from livekit.agents import inference, tts
from livekit.plugins import elevenlabs

logger = logging.getLogger(__name__)


# Default voice when a dispatch does not select one.
DEFAULT_TTS_VOICE_ID = "QTKSa2Iyv0yoxvXY2V8a"
# Use the streaming Flash model for low latency. It does not normalize numbers
# on its own, so prompts spell identifiers as words (prompts/verification.md,
# Speech delivery).
# Model and listening history: docs/decisions.md#voice-and-spoken-identifiers.
TTS_MODEL = "eleven_flash_v2_5"

# Pin settings so calls do not depend on inherited provider defaults. Stability
# keeps short replies even; use these same values for playground comparisons.
TTS_VOICE_SETTINGS = elevenlabs.VoiceSettings(
    stability=0.7, similarity_boost=0.4, style=0.0, speed=1.02, use_speaker_boost=True
)


def tts_chain(voice_id: str | None = None) -> list[tts.TTS]:
    """The TTS fallback chain, primary first.

    The direct ElevenLabs plugin requires ELEVEN_API_KEY at construction, before
    fallback can run. Skip it when the key is absent so the call can use xAI.

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
