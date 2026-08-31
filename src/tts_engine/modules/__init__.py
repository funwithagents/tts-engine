"""TTS module registry and load_module()."""
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSModule
from tts_engine.modules.elevenlabs import ElevenLabsModule

REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,
}


def load_module(tts_config: dict) -> TTSModule:
    module_type = tts_config.get("type")
    if module_type not in REGISTRY:
        raise ConfigError(f"Unknown TTS module type: {module_type!r}")
    cls = REGISTRY[module_type]
    return cls(tts_config)
