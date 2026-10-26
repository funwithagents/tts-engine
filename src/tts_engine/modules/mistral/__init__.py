"""Mistral module — Voxtral TTS through the mistralai SDK (registry key ``"mistral"``).

Behind the ``mistral`` extra. See ``module.py`` and specs/modules/mistral.md.
"""

from tts_engine.modules.mistral.module import MistralModule

__all__ = ["MistralModule"]
