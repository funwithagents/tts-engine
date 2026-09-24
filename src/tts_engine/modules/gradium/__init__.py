"""Gradium streaming TTS module — an API-backed provider (registry key ``"gradium"``).

Behind the ``gradium`` extra. See ``module.py`` and specs/modules/gradium.md.
"""

from tts_engine.modules.gradium.module import GradiumModule

__all__ = ["GradiumModule"]
