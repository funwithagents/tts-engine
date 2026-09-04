"""TTS Engine — a streaming text-to-speech engine, usable as a library or MCP server."""

import logging

from tts_engine.audio import AudioSink
from tts_engine.config import TTSEngineConfig, load_config
from tts_engine.engine import TTSEngine
from tts_engine.tools import TTSTools

# Well-behaved library: attach a NullHandler to the package logger so importing
# tts_engine never emits output or configures logging. The application (e.g. the
# MCP entry point) decides where records go and at what level — never the library.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["AudioSink", "TTSEngine", "TTSEngineConfig", "TTSTools", "load_config"]
