"""Provider- and transport-agnostic tools over a TTSEngine."""

import logging

from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError

log = logging.getLogger(__name__)


async def speak(engine: TTSEngine, text: str) -> str:
    """Synthesize `text` and play it via `engine`.

    Returns "OK" on success, or "TTS error: <message>" if the text is empty
    or synthesis fails. Does not raise for these expected conditions; callers
    wanting the raw, exception-raising behavior call `engine.speak` directly.
    """
    if not text:
        return "TTS error: text must not be empty"
    try:
        await engine.speak(text)
        return "OK"
    except TTSError as e:
        log.error("TTS error: %s", e)
        return f"TTS error: {e}"
