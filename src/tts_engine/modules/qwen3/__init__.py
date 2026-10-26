"""Qwen3 module — Qwen3-TTS via qwentts.cpp (registry key ``"qwen3"``).

Behind the ``qwen3`` extra. See ``module.py`` and specs/modules/qwen3.md.
"""

from tts_engine.modules.qwen3.module import Qwen3Module

__all__ = ["Qwen3Module"]
