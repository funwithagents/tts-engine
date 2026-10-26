"""TTSEngine: builds module + sink from config, exposes say() and say_stream()."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Iterable

from tts_engine.audio import AudioPlayer, AudioSink
from tts_engine.config import TTSEngineConfig
from tts_engine.modules import load_module
from tts_engine.modules.base import TTSOptions


def _as_async_iterator(
    text_stream: AsyncIterable[str] | Iterable[str],
) -> AsyncIterator[str]:
    """Hand an async iterable through; wrap a plain iterable (an in-memory
    sequence) in an async generator so the module sees one shape."""
    if isinstance(text_stream, AsyncIterable):
        return text_stream.__aiter__()

    async def _wrap() -> AsyncIterator[str]:
        for piece in text_stream:
            yield piece

    return _wrap()


class TTSEngine:
    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        """Build the module (via load_module) from config. Use the injected sink,
        or build the default local AudioPlayer from config when sink is None.

        When a sink is injected, no AudioPlayer is constructed and sounddevice is
        never imported — the engine stays usable on hosts with no audio device.
        """
        self._module = load_module(config.module)
        self._sink: AudioSink = (
            sink
            if sink is not None
            else AudioPlayer(
                device=config.player.device, sample_rate=self._module.sample_rate
            )
        )
        self._say_lock = asyncio.Lock()

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) of the PCM chunks fed to the sink — the active
        module's declared rate. Fixed for the engine's lifetime."""
        return self._module.sample_rate

    async def say(self, text: str) -> None:
        """Synthesize the complete `text` and feed it to the sink."""
        async with self._say_lock:
            options = TTSOptions()
            try:
                await self._module.synthesize_text(
                    text, options, callback=self._sink.feed
                )
            finally:
                self._sink.drain()

    async def say_stream(self, text_stream: AsyncIterable[str] | Iterable[str]) -> None:
        """Synthesize text as it arrives from `text_stream` (an LLM response,
        token by token) and feed audio to the sink while the stream is still
        being produced.

        `text_stream` is an async iterable of text pieces. A plain iterable is
        accepted for in-memory sequences only: it is consumed on the event loop,
        so a blocking sync generator would stall it; make such a source async.
        A bare `str` is rejected (use `say`). Like `say`, the whole utterance is
        one exclusive, single-drain pass on the sink; the lock is held for the
        full length of the stream plus the audio tail.

        Raises `TTSError` on synthesis failure; an exception raised by the
        stream itself aborts the utterance and propagates unchanged.
        """
        if isinstance(text_stream, str):
            raise TypeError("say_stream takes an iterable of text pieces, not a str")
        source = _as_async_iterator(text_stream)
        async with self._say_lock:
            options = TTSOptions()
            try:
                await self._module.synthesize_text_stream(
                    source, options, callback=self._sink.feed
                )
            finally:
                self._sink.drain()
