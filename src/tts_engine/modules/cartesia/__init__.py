"""Cartesia Sonic streaming TTS module — an API-backed provider (registry key ``"cartesia"``).

Behind the ``cartesia`` extra. See ``module.py`` and specs/modules/cartesia.md.
"""

from tts_engine.modules.cartesia.module import CartesiaModule

__all__ = ["CartesiaModule"]
