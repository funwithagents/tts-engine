"""Gemini module — Gemini TTS through the google-genai SDK (registry key ``"gemini"``).

Behind the ``gemini`` extra. See ``module.py`` and specs/modules/gemini.md.
"""

from tts_engine.modules.gemini.module import GeminiModule

__all__ = ["GeminiModule"]
