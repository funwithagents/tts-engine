"""ElevenLabs streaming TTS module — an API-backed provider (registry key ``"elevenlabs"``).

Behind the ``elevenlabs`` extra. See ``module.py`` and specs/modules/elevenlabs.md.
"""

from tts_engine.modules.elevenlabs.module import ElevenLabsModule

__all__ = ["ElevenLabsModule"]
