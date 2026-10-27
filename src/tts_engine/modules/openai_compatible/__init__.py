"""OpenAI-compatible module — /v1/audio/speech through the openai SDK (registry key
``"openai-compatible"``).

Behind the ``openai-compatible`` extra. See ``module.py`` and
specs/modules/openai-compatible.md.
"""

from tts_engine.modules.openai_compatible.module import OpenAICompatibleModule

__all__ = ["OpenAICompatibleModule"]
