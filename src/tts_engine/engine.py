"""TTSEngine: builds module + sink from config, exposes say() and say_stream()."""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Self

from tts_engine.audio import AudioPlayer, AudioSink
from tts_engine.config import TTSEngineConfig
from tts_engine.modules import load_module
from tts_engine.modules.base import TTSOptions, cleanup

log = logging.getLogger(__name__)


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
        self._closed = False

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) of the PCM chunks fed to the sink — the active
        module's declared rate. Fixed for the engine's lifetime."""
        return self._module.sample_rate

    async def close(self) -> None:
        """Release the module the engine built: its SDK client or loaded
        model. Waits for an utterance in flight to finish first (it takes the
        say lock), then closes the module once; later calls do nothing, and
        `say`/`say_stream` raise `RuntimeError`. The sink is not touched: an
        injected sink is the embedder's, and the default player holds nothing
        between utterances.
        """
        async with self._say_lock:
            if self._closed:
                return
            self._closed = True
            self._module.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("TTSEngine is closed")

    async def say(self, text: str) -> None:
        """Synthesize the complete `text` and feed it to the sink.

        The sink is drained exactly once, after the last `feed`, on every
        path. `drain` runs synchronously on the calling loop, so a sink whose
        drain blocks (the default player waits for its buffered audio) holds
        the loop for that long. Synthesis, callback and cancellation outcomes
        take precedence over a failing drain, which is then only logged.
        Raises `RuntimeError` once the engine is closed.
        """
        async with self._say_lock:
            self._check_open()
            options = TTSOptions()
            with cleanup(self._sink.drain, "sink drain"):
                await self._module.synthesize_text(
                    text, options, callback=self._sink.feed
                )

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
        stream itself aborts the utterance and propagates unchanged. The sink
        is drained once at the end, as for `say` (same drain rules). The
        engine owns the iterator for the utterance: once it is over, on every
        path, the iterator's `aclose()` is awaited when it has one (an async
        generator's `finally` runs then), and a failing `aclose` never hides
        the utterance's own outcome. Raises `RuntimeError` once closed.
        """
        if isinstance(text_stream, str):
            raise TypeError("say_stream takes an iterable of text pieces, not a str")
        source = _as_async_iterator(text_stream)
        async with self._say_lock:
            self._check_open()
            options = TTSOptions()
            try:
                with cleanup(self._sink.drain, "sink drain"):
                    await self._module.synthesize_text_stream(
                        source, options, callback=self._sink.feed
                    )
            except BaseException:
                await _aclose(source, failed=True)
                raise
            else:
                await _aclose(source, failed=False)


async def _aclose(source: AsyncIterator[str], *, failed: bool) -> None:
    """Close the text source at the end of its utterance, the `cleanup` rule
    applied to an async close: after a failure its own error is only logged."""
    aclose = getattr(source, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        if not failed:
            raise
        log.warning("text source: aclose failed after an error", exc_info=True)
