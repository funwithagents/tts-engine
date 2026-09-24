"""Pocket TTS module — a local, in-process model provider (registry key ``"pocket"``).

Behind the ``pocket`` extra. See ``module.py`` and specs/modules/pocket.md.
"""

from tts_engine.modules.pocket.module import PocketModule

__all__ = ["PocketModule"]
