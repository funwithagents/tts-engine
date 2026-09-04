"""AudioSink protocol + AudioPlayer: sounddevice-based streaming playback."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    import sounddevice as sd

log = logging.getLogger(__name__)

_CHANNELS = 1
_DTYPE = "int16"


class AudioSink(Protocol):
    """Destination for synthesized PCM chunks.

    Chunks are raw signed-16-bit little-endian **mono** PCM at the active
    module's declared `sample_rate` (the same bytes `AudioPlayer.feed`
    receives). A sink that needs a different rate or dtype converts internally.

    `feed` is called sequentially with each chunk — calls may run off the
    event-loop thread but never overlap. `drain` is called exactly once after
    the last `feed`, including on synthesis failure or cancellation.
    """

    def feed(self, chunk: bytes) -> None: ...
    def drain(self) -> None: ...


class AudioPlayer(AudioSink):
    def __init__(self, sample_rate: int, device: str | int | None = None) -> None:
        self._sample_rate = sample_rate
        self._device = device
        self._stream: sd.OutputStream | None = None

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._stream is None:
            # Imported lazily (not at module top) so `import tts_engine` and an
            # engine driven by a custom sink work on hosts with no PortAudio.
            import sounddevice as sd

            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=_CHANNELS,
                dtype=_DTYPE,
                device=self._device,
            )
            self._stream.start()
        array = np.frombuffer(chunk, dtype=np.int16)
        self._stream.write(array)

    def drain(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
