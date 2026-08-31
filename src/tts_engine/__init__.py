"""TTS Engine — a streaming text-to-speech engine, usable as a library or MCP server."""

from tts_engine.config import TTSEngineConfig, load_config
from tts_engine.engine import TTSEngine

__all__ = ["TTSEngine", "TTSEngineConfig", "load_config"]
