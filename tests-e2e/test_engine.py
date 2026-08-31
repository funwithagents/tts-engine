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
