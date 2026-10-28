"""Tone fixture module: a sine tone proportional to the text length.

Not a TTS backend. Exists so the engine, tools, MCP transport, and downstream
test suites run end to end with no API key, extra, model, or fixture files.
"""

import threading
from collections.abc import AsyncIterator, Callable, Iterator

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSModule,
    TTSOptions,
    VoiceInfo,
    finite_number,
    run_cancellable_worker,
    run_text_stream_worker,
)

_CHUNK_FRAMES = 4096
_LABEL = "Tone module"


class ToneModule(TTSModule):
    kind = "fixture"

    def __init__(self, config: dict) -> None:
        sample_rate = config.get("sample_rate", 24000)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate <= 0
        ):
            raise ConfigError("Tone module 'sample_rate' must be an integer > 0")
        self._sample_rate = sample_rate
        self._frequency = finite_number(
            config, "frequency", label=_LABEL, default=440.0, exclusive_minimum=0
        )
        self._seconds_per_char = finite_number(
            config, "seconds_per_char", label=_LABEL, default=0.05, exclusive_minimum=0
        )
        self._amplitude = finite_number(
            config,
            "amplitude",
            label=_LABEL,
            default=0.2,
            exclusive_minimum=0,
            maximum=1.0,
        )

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """A fixture has no voice to choose."""
        return []

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _frames_for(self, text: str) -> int:
        return round(len(text) * self._seconds_per_char * self._sample_rate)

    def _emit(
        self,
        start: int,
        end: int,
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> int:
        """Feed frames [start, end) in chunks; return the frame index reached.

        Frame indices run continuously across chunks (and across pieces of a
        text stream), so the wave has no phase jump at any boundary.
        """
        while start < end and not stop.is_set():
            chunk_end = min(start + _CHUNK_FRAMES, end)
            n = np.arange(start, chunk_end, dtype=np.float64)
            wave = np.sin(2.0 * np.pi * self._frequency * n / self._sample_rate)
            pcm = np.round(self._amplitude * 32767.0 * wave).astype("<i2")
            callback(pcm.tobytes())
            start = chunk_end
        return start

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        total = self._frames_for(text)

        def _worker(stop: threading.Event) -> None:
            self._emit(0, total, callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # A tone per piece, as each arrives: no grouping, so the first piece's
        # audio is fed before the source has produced the second.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            frame = 0
            for piece in pieces:
                frame = self._emit(
                    frame, frame + self._frames_for(piece), callback, stop
                )

        await run_text_stream_worker(text_stream, _worker)
