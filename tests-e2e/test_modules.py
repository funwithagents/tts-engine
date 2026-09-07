"""Per-module live conformance, driven engine-direct (no MCP server).

Parametrized over ``MODULES`` in ``support`` — every backend runs both scenarios
with its own config:

- ``test_module_say_produces_pcm`` — synthesize into an injected ``AudioSink``
  and assert whole signed-16-bit PCM was produced and drained once. No audio
  hardware.
- ``test_module_say_completes`` — the same synthesis played through the real
  ``AudioPlayer`` (the default sink), exercising the output stream opened at the
  module's declared sample rate. Needs audio hardware.

The MCP transport is module-agnostic and covered once in ``test_mcp.py``.

Run one backend with ``-k``, e.g. ``uv run pytest tests-e2e -k elevenlabs``.
Each row skips cleanly when its key env var is unset or its extra is absent
(see ``support.require_module``).
"""

from __future__ import annotations

import pytest
from support import MODULES, CaptureSink, engine_block, require_module

from tts_engine import TTSEngine
from tts_engine.config import TTSEngineConfig


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
    require_module(module_type, module_config)

    engine_cfg = TTSEngineConfig.from_dict(engine_block(module_type, module_config))
    engine = TTSEngine(engine_cfg)

    await engine.say("Hello directly from the TTS engine")
