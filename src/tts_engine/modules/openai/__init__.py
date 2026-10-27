"""OpenAI module — the Realtime API through the openai SDK (registry key
``"openai"``).

Behind the ``openai`` extra. See ``module.py`` and specs/modules/openai.md.
"""

from tts_engine.modules.openai.module import OpenAIModule

__all__ = ["OpenAIModule"]
