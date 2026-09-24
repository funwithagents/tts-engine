"""Tone fixture module — a test fixture, not a TTS backend (registry key ``"tone"``).

Base install, no extra. See ``module.py`` and specs/modules/tone.md.
"""

from tts_engine.modules.tone.module import ToneModule

__all__ = ["ToneModule"]
