"""OpenAI speech-compatible module — /v1/audio/speech through the openai SDK (registry key
``"openai-speech-compatible"``).

Behind the ``openai-compatible`` extra. See ``module.py`` and
specs/modules/openai-speech-compatible.md.
"""

from tts_engine.modules.openai_speech_compatible.module import (
    OpenAISpeechCompatibleModule,
)

__all__ = ["OpenAISpeechCompatibleModule"]
