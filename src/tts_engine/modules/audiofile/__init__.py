"""AudioFile fixture module — a test fixture, not a TTS backend (registry key ``"audiofile"``).

Base install, no extra. See ``module.py`` and specs/modules/audiofile.md.
"""

from tts_engine.modules.audiofile.module import AudioFileModule

__all__ = ["AudioFileModule"]
