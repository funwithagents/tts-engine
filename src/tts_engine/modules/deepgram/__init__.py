"""Deepgram Aura streaming TTS module — an API-backed provider (registry key ``"deepgram"``).

Behind the ``deepgram`` extra. See ``module.py`` and specs/modules/deepgram.md.
"""

from tts_engine.modules.deepgram.module import DeepgramModule

__all__ = ["DeepgramModule"]
