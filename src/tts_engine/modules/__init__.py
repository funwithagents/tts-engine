"""TTS module registry and load_module().

Each backend is a package of its own under ``modules/`` (``elevenlabs/``,
``pocket/``, ``gradium/``, ``phonon/``, ``mistral/``, ``deepgram/``,
``gemini/``, ``cartesia/``, ``openai/``, ``qwen3/``, ``kokoro/``, ``tone/``,
``audiofile/``), its ``TTSModule`` subclass in ``module.py`` and re-exported
from the package ``__init__``; the registry imports every class eagerly, so
providers import their library lazily inside ``__init__`` (see
specs/tts-module-interface.md, "Module registry").
"""

from tts_engine.config import ConfigError
from tts_engine.modules.audiofile import AudioFileModule
from tts_engine.modules.base import TTSModule
from tts_engine.modules.cartesia import CartesiaModule
from tts_engine.modules.deepgram import DeepgramModule
from tts_engine.modules.elevenlabs import ElevenLabsModule
from tts_engine.modules.gemini import GeminiModule
from tts_engine.modules.gradium import GradiumModule
from tts_engine.modules.kokoro import KokoroModule
from tts_engine.modules.mistral import MistralModule
from tts_engine.modules.openai import OpenAIModule
from tts_engine.modules.phonon import PhononModule
from tts_engine.modules.pocket import PocketModule
from tts_engine.modules.qwen3 import Qwen3Module
from tts_engine.modules.tone import ToneModule

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
    "qwen3": Qwen3Module,
    "kokoro": KokoroModule,
    "tone": ToneModule,
    "audiofile": AudioFileModule,
}


def load_module(tts_config: dict) -> TTSModule:
    module_type = tts_config.get("type")
    if module_type not in REGISTRY:
        raise ConfigError(f"Unknown TTS module type: {module_type!r}")
    cls = REGISTRY[module_type]
    return cls(tts_config)
