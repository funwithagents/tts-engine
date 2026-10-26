"""Per-module live conformance, driven engine-direct (no MCP server).

Parametrized over ``MODULES`` in ``support`` — every backend runs three scenarios
with its own config:

- ``test_module_say_produces_pcm`` — synthesize into an injected ``AudioSink``
  and assert whole signed-16-bit PCM was produced and drained once. No audio
  hardware.
- ``test_module_say_completes`` — the same synthesis played through the real
  ``AudioPlayer`` (the default sink), exercising the output stream opened at the
  module's declared sample rate. Needs audio hardware.
- ``test_module_say_stream_produces_pcm`` — ``say_stream`` fed from a small
  async source of text pieces into an injected ``AudioSink``; whole PCM, drained
  once for the whole stream. No audio hardware.

The MCP transport is module-agnostic and covered once in ``test_mcp.py``.

Run one backend with ``-k``, e.g. ``uv run pytest tests-e2e -k elevenlabs``.
Each row skips cleanly when its key env var is unset or its extra is absent
(see ``support.require_module``).
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from support import MODULES, CaptureSink, engine_block, require_module, spoken_text

from tts_engine import TTSEngine
from tts_engine.config import TTSEngineConfig

log = logging.getLogger(__name__)


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_say_produces_pcm(module_type: str, module_config: dict) -> None:
    # The real backend stream reaches an injected sink through the public seam.
    # Assert robust properties only (no audio content): bytes were produced, in
    # whole int16 samples, drained once. Needs no audio hardware.
    require_module(module_type, module_config)

    sink = CaptureSink()
    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg, sink=sink)

    await engine.say("Conformance check for the TTS module")

    assert len(sink.data) > 0
    assert len(sink.data) % 2 == 0  # whole signed-16-bit samples
    assert sink.drains == 1
    assert engine.sample_rate > 0  # module-declared rate the sink would resample from


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_say_completes(module_type: str, module_config: dict) -> None:
    # The full library path with the default AudioPlayer sink: synthesis plays
    # through real audio hardware at the module's declared rate. Asserts only
    # that the pass completes without raising (no audio-content verification).
    # The text names the module, so whoever is listening to the run knows
    # which backend is speaking.
    require_module(module_type, module_config)

    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg)

    await engine.say(spoken_text(module_type))


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_say_stream_produces_pcm(
    module_type: str, module_config: dict
) -> None:
    # The text-stream input shape against the real backend: a few pieces, two
    # sentences, one split mid-word, so both the native (socket) and the
    # sentence-grouping modules are exercised. Assert robust properties only:
    # PCM was produced, in whole int16 samples, drained once for the whole
    # stream. Needs no audio hardware.
    require_module(module_type, module_config)

    async def pieces():
        for piece in [
            "Hello directly ",
            "from the TTS engine. ",
            "This is the sec",
            "ond sentence.",
        ]:
            yield piece

    sink = CaptureSink()
    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg, sink=sink)

    await engine.say_stream(pieces())

    assert len(sink.data) > 0
    assert len(sink.data) % 2 == 0  # whole signed-16-bit samples
    assert sink.drains == 1


# --- cancellation ------------------------------------------------------------

# Long enough that the audio outlasts the cancel; the first sentence alone
# passes ElevenLabs's 50-character floor so the stream path starts on it.
_FIRST_SENTENCE = (
    "Hello directly from the TTS engine, speaking a longer first sentence "
    "that keeps going for a while. "
)
_LONG_TEXT = _FIRST_SENTENCE + (
    "Then a second sentence follows, and a third one after that, so there is "
    "plenty of audio left when the call is cancelled."
)


class _PacedCaptureSink(CaptureSink):
    """A capture sink paced like a sound device (50 ms per chunk), so the
    utterance is still in progress when the test cancels it."""

    def feed(self, chunk: bytes) -> None:
        super().feed(chunk)
        time.sleep(0.05)


async def _cancel_after_first_chunk(task: asyncio.Task, sink: CaptureSink) -> float:
    """Cancel once audio is flowing; return how long the cancel took to
    surface. Asserts nothing is fed after it surfaced."""
    while not sink.data:
        await asyncio.sleep(0.01)
    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=20)
    elapsed = time.perf_counter() - t0

    settled = len(sink.data)
    await asyncio.sleep(0.5)
    assert len(sink.data) == settled
    assert sink.drains == 1
    return elapsed


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_say_cancel_stops_feeding(
    module_type: str, module_config: dict
) -> None:
    # Cancel `say` mid-utterance against the real backend: the engine must
    # stop feeding before CancelledError surfaces and drain the sink once.
    require_module(module_type, module_config)
    sink = _PacedCaptureSink()
    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg, sink=sink)

    task = asyncio.create_task(engine.say(_LONG_TEXT))
    elapsed = await _cancel_after_first_chunk(task, sink)
    log.info("%s: say cancel surfaced after %.2f s", module_type, elapsed)


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_say_stream_cancel_stops_feeding(
    module_type: str, module_config: dict
) -> None:
    # Cancel `say_stream` while the first sentence plays and the source is
    # still open (an LLM mid-answer): same guarantees as for `say`.
    require_module(module_type, module_config)
    sink = _PacedCaptureSink()
    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg, sink=sink)

    async def source():
        for word in _FIRST_SENTENCE.split(" "):
            yield word + " "
        await asyncio.Event().wait()  # the source stays open

    task = asyncio.create_task(engine.say_stream(source()))
    elapsed = await _cancel_after_first_chunk(task, sink)
    log.info("%s: say_stream cancel surfaced after %.2f s", module_type, elapsed)
