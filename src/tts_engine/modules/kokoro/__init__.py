"""Kokoro module — a local, in-process model provider (registry key ``"kokoro"``).

Behind the ``kokoro`` extra. See ``module.py`` and specs/modules/kokoro.md.
"""

from tts_engine.modules.kokoro.module import KokoroModule

__all__ = ["KokoroModule"]
