"""Live check of ``engine.player.discard``: a real backend, no device, real time.

The local ``phonon`` model (no API key) speaks through an engine whose config
discards its audio. ``say`` must last about as long as the audio phonon
produced — the ``DiscardPlayer`` keeps a device's pace on the real clock — and
no output stream is ever opened. Skips when the ``phonon`` extra is absent; the
first run downloads its model.

Run it with ``uv run pytest tests-e2e -k discard -rs``.
"""

from __future__ import annotations

import logging
import time

import pytest
from support import require_module

from tts_engine import TTSEngine
from tts_engine.audio import DiscardPlayer
from tts_engine.config import TTSEngineConfig

log = logging.getLogger(__name__)

# How much longer than its audio `say` may take: the model's time to first
# audio, the thread hand-offs, a slow runner.
_SLACK_SECONDS = 1.5
# How much shorter: float rounding and the clock's resolution, nothing more.
_EPSILON_SECONDS = 0.05


async def test_discard_config_speaks_for_the_audio_duration_on_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_module("phonon", {})

    # Count what reaches the engine-built DiscardPlayer, its pace untouched.
    fed = bytearray()
    original_feed = DiscardPlayer.feed

    def counting_feed(self: DiscardPlayer, chunk: bytes) -> None:
        fed.extend(chunk)
        original_feed(self, chunk)

    monkeypatch.setattr(DiscardPlayer, "feed", counting_feed)

    # Any output stream opened would be recorded here instead of reaching a device.
    opened: list[object] = []
    try:
        import sounddevice
    except (ImportError, OSError):  # no PortAudio: nothing could be opened anyway
        pass
    else:
        monkeypatch.setattr(
            sounddevice, "OutputStream", lambda *a, **k: opened.append((a, k))
        )

    engine = TTSEngine(
        TTSEngineConfig.from_dict(
            {"module": {"type": "phonon"}, "player": {"discard": True}}
        )
    )
    try:
        start = time.monotonic()
        await engine.say("The voice goes nowhere, but it still takes its time.")
        elapsed = time.monotonic() - start
    finally:
        await engine.close()

    audio_seconds = len(fed) / 2 / engine.sample_rate
    log.info(
        "phonon via discard: %.2f s of audio, say took %.2f s", audio_seconds, elapsed
    )
    assert audio_seconds > 1.0  # a real sentence was spoken
    assert audio_seconds - _EPSILON_SECONDS <= elapsed <= audio_seconds + _SLACK_SECONDS
    assert opened == []
