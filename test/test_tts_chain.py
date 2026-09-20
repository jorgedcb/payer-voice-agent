"""The voice is a property of the call, not of the deployed worker.

A call triggered with a voice id speaks with it; one triggered without falls back
to the worker's default, and a worker with no ElevenLabs key still has a chain.
"""

import pytest

from dispatch import sample_spec
from voices import DEFAULT_TTS_VOICE_ID, TTS_MODEL, TTS_VOICE_SETTINGS, tts_chain


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    # Construction only: both clients read a key at __init__ and neither is used.
    monkeypatch.setenv("ELEVEN_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")


def test_the_spec_picks_the_voice() -> None:
    assert tts_chain("BIvP0GN1cAtSRTxNHnWS")[0]._opts.voice_id == "BIvP0GN1cAtSRTxNHnWS"


def test_no_voice_on_the_spec_uses_the_workers_default() -> None:
    assert (
        tts_chain(sample_spec().tts_voice_id)[0]._opts.voice_id == DEFAULT_TTS_VOICE_ID
    )


def test_the_primary_speaks_flash_v2_5() -> None:
    # The prompt spells identifiers out as words because this model does not
    # normalize numbers itself; a model change is a prompt change.
    primary = tts_chain(None)[0]
    assert primary._opts.model == TTS_MODEL == "eleven_flash_v2_5"


def test_a_worker_without_a_key_still_has_the_fallbacks(monkeypatch) -> None:
    # The primary raises when constructed without a key, and that raise would land
    # inside the job: every dispatched call would die before a word was spoken.
    monkeypatch.delenv("ELEVEN_API_KEY")

    chain = tts_chain("BIvP0GN1cAtSRTxNHnWS")

    assert len(chain) == 2
    assert not any(
        hasattr(tts, "_opts") and hasattr(tts._opts, "voice_id") for tts in chain[:1]
    )


def test_voice_settings_are_pinned_on_every_voice() -> None:
    # An unset voice_settings goes out as an empty object, and what ElevenLabs does
    # with that is undocumented; pinning them is what makes a call reproducible in
    # the playground. A per-call voice gets the same settings as the default.
    for voice in (None, "BIvP0GN1cAtSRTxNHnWS"):
        assert tts_chain(voice)[0]._opts.voice_settings == TTS_VOICE_SETTINGS
    assert TTS_VOICE_SETTINGS.stability >= 0.7
