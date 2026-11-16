"""TTS module registry and load_module().

Each backend is a package of its own under ``modules/`` (``elevenlabs/``,
``pocket/``, ``gradium/``, ``phonon/``, ``mistral/``, ``deepgram/``,
``gemini/``, ``cartesia/``, ``openai/``, ``openai_speech_compatible/``, ``qwen3/``,
``kokoro/``, ``piper/``, ``tone/``, ``audiofile/``), its ``TTSModule`` subclass in ``module.py`` and
re-exported from the package ``__init__``; the registry imports every class
eagerly, so providers import their library lazily inside ``__init__`` (see
specs/tts-module-interface.md, "Module registry"). A renamed type keeps
working through ``RENAMED`` (old → new), with a deprecation warning.
"""

import logging

from tts_engine.config import ConfigError
from tts_engine.modules.audiofile import AudioFileModule
from tts_engine.modules.base import TTSModule, VoiceInfo
from tts_engine.modules.cartesia import CartesiaModule
from tts_engine.modules.deepgram import DeepgramModule
from tts_engine.modules.elevenlabs import ElevenLabsModule
from tts_engine.modules.gemini import GeminiModule
from tts_engine.modules.gradium import GradiumModule
from tts_engine.modules.kokoro import KokoroModule
from tts_engine.modules.mistral import MistralModule
from tts_engine.modules.openai import OpenAIModule
from tts_engine.modules.openai_speech_compatible import OpenAISpeechCompatibleModule
from tts_engine.modules.phonon import PhononModule
from tts_engine.modules.piper import PiperModule
from tts_engine.modules.pocket import PocketModule
from tts_engine.modules.qwen3 import Qwen3Module
from tts_engine.modules.tone import ToneModule

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,
    "pocket": PocketModule,
    "gradium": GradiumModule,
    "phonon": PhononModule,
    "mistral": MistralModule,
    "deepgram": DeepgramModule,
    "gemini": GeminiModule,
    "cartesia": CartesiaModule,
    "openai": OpenAIModule,
    "openai-speech-compatible": OpenAISpeechCompatibleModule,
    "qwen3": Qwen3Module,
    "kokoro": KokoroModule,
    "piper": PiperModule,
    "tone": ToneModule,
    "audiofile": AudioFileModule,
}


# Old module types still accepted, each mapped to its current name (see
# specs/tts-module-interface.md, "Module names and renamed types").
RENAMED: dict[str, str] = {
    "openai-compatible": "openai-speech-compatible",
}


def resolve_type(module_type: object) -> str:
    """The registered type for ``module_type``: a renamed type is mapped to its
    current name with a deprecation warning; an unknown one raises ``ConfigError``."""
    if isinstance(module_type, str) and module_type in RENAMED:
        new_type = RENAMED[module_type]
        log.warning(
            "TTS module type %r is deprecated, use %r instead", module_type, new_type
        )
        module_type = new_type
    if not isinstance(module_type, str) or module_type not in REGISTRY:
        raise ConfigError(f"Unknown TTS module type: {module_type!r}")
    return module_type


def load_module(tts_config: dict) -> TTSModule:
    cls = REGISTRY[resolve_type(tts_config.get("type"))]
    return cls(tts_config)


def list_voices(module_config: dict) -> list[VoiceInfo]:
    """The voices of the backend named by ``module_config["type"]`` (see
    ``TTSModule.list_voices``)."""
    return REGISTRY[resolve_type(module_config.get("type"))].list_voices(module_config)
