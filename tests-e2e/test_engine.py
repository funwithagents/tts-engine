"""E2E test for the library interface: `TTSEngine(config)` → `speak`.

Drives the engine in-process, the way a library caller would — no MCP
transport in the loop. It hits the real ElevenLabs API and the machine's audio
output; the property asserted is that a full synthesis-and-playback pass
completes without raising (no audio-content verification). The MCP transport is
covered separately in `test_mcp.py`.
"""

from __future__ import annotations

from tts_engine import TTSEngine


async def test_engine_speak_completes(app_config):
    engine = TTSEngine(app_config.engine)
    await engine.speak("Hello directly from the TTS engine")


class _CaptureSink:
    """In-memory AudioSink: keeps the synthesized PCM instead of playing it."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.drains = 0

    def feed(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    def drain(self) -> None:
        self.drains += 1


async def test_engine_speak_into_custom_sink_produces_pcm(app_config):
    # The real ElevenLabs stream → miniaudio decode reaches an injected sink
    # through the public seam. Assert robust properties only (no audio content):
    # bytes were produced, whole int16 samples, drained once. Needs no audio hw.
    sink = _CaptureSink()
    engine = TTSEngine(app_config.engine, sink=sink)

    await engine.speak("Captured straight from the TTS engine")

    assert len(sink.data) > 0
    assert len(sink.data) % 2 == 0  # whole signed-16-bit samples
    assert sink.drains == 1
    assert engine.sample_rate > 0  # module-declared rate the sink would resample from
