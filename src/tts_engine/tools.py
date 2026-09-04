"""Provider- and transport-agnostic tools over a TTSEngine.

`TTSTools` binds an engine and exposes each operation as a method whose
signature and docstring are meant to be used *as-is* as an agent tool: the
MCP server wraps these methods, a non-MCP agent can register a bound method
(e.g. `TTSTools(engine).say`) directly, and library code can call them for
the guarded, non-raising contract instead of raw `engine.say`.
"""

import logging

from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError

log = logging.getLogger(__name__)


class TTSTools:
    """Engine-bound TTS tools, ready to register with an agent.

    Construct once with an engine; each method takes only its own inputs, so a
    bound method (`TTSTools(engine).say`) is directly usable as a tool — its
    `__name__`, docstring, and signature describe the tool to the model.
    """

    def __init__(self, engine: TTSEngine) -> None:
        self._engine = engine

    async def say(self, text: str) -> str:
        """Synthesize `text` and play it on the machine running the engine.

        Returns "OK" on success, or "TTS error: <message>" if the text is empty
        or synthesis fails. Does not raise for these expected conditions; callers
        wanting the raw, exception-raising behavior call `engine.say` directly.
        """
        if not text:
            return "TTS error: text must not be empty"
        try:
            await self._engine.say(text)
            return "OK"
        except TTSError as e:
            log.error("TTS error: %s", e)
            return f"TTS error: {e}"
