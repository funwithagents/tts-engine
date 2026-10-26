"""Phonon module — Gradium's on-device model via ptts (registry key ``"phonon"``).

Behind the ``phonon`` extra. See ``module.py`` and specs/modules/phonon.md.
"""

from tts_engine.modules.phonon.module import PhononModule

__all__ = ["PhononModule"]
