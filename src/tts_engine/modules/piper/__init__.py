"""Piper module — a local, in-process model provider (registry key ``"piper"``).

Behind the ``piper`` extra. See ``module.py`` and specs/modules/piper.md.
"""

from tts_engine.modules.piper.module import PiperModule

__all__ = ["PiperModule"]
