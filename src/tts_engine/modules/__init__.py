"""TTS module registry and load_module()."""

from tts_engine.config import ConfigError
from tts_engine.modules.audiofile import AudioFileModule
from tts_engine.modules.base import TTSModule
from tts_engine.modules.elevenlabs import ElevenLabsModule
from tts_engine.modules.gradium import GradiumModule
from tts_engine.modules.pocket import PocketModule
from tts_engine.modules.tone import ToneModule

REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,
    "pocket": PocketModule,
    "gradium": GradiumModule,
    "tone": ToneModule,
    "audiofile": AudioFileModule,
}


def load_module(tts_config: dict) -> TTSModule:
    module_type = tts_config.get("type")
    if module_type not in REGISTRY:
        raise ConfigError(f"Unknown TTS module type: {module_type!r}")
    cls = REGISTRY[module_type]
    return cls(tts_config)
