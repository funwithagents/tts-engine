"""AudioSink protocol + AudioPlayer (sounddevice-based streaming playback) and
DiscardPlayer (no device, real-time pace)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    import sounddevice as sd

log = logging.getLogger(__name__)

_CHANNELS = 1
_DTYPE = "int16"
_SAMPLE_BYTES = 2

# How far ahead of real time `DiscardPlayer.feed` lets the fed audio run before
# it blocks: the stand-in for a sound device's output buffer.
DISCARD_BUFFER_SECONDS = 0.1


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

            stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=_CHANNELS,
                dtype=_DTYPE,
                device=self._device,
            )
            try:
                stream.start()
            except Exception:
                # Never leave a half-opened stream attached: release the
                # device, and let the next feed() try afresh.
                stream.close()
                raise
            self._stream = stream
        array = np.frombuffer(chunk, dtype=np.int16)
        self._stream.write(array)

    def drain(self) -> None:
        # Detach first so a failing stop() cannot leave a stale stream on the
        # player; close() always runs so the device is released either way.
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()


class DiscardPlayer(AudioSink):
    """A player with no device: the audio goes nowhere, at real-time pace.

    Built by the engine for ``engine.player.discard``. It opens no device and
    never imports sounddevice, but it keeps the timing of `AudioPlayer`, since
    callers rely on it (an utterance lasts as long as its audio; a cancel lands
    mid-speech): ``feed`` blocks as a blocking device write does, until the
    audio fed so far is at most ``DISCARD_BUFFER_SECONDS`` ahead of real time,
    and ``drain`` waits for the rest to "play out", then starts the next
    utterance afresh. The pace runs against one deadline (the utterance's start
    plus the duration fed so far), so per-call overhead never accumulates; a
    producer slower than real time is an underrun, as on a device: the deadline
    restarts from now.
    """

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._deadline: float | None = None

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        now = time.monotonic()
        start = now if self._deadline is None else max(self._deadline, now)
        self._deadline = start + len(chunk) / _SAMPLE_BYTES / self._sample_rate
        _sleep_until(self._deadline - DISCARD_BUFFER_SECONDS)

    def drain(self) -> None:
        deadline, self._deadline = self._deadline, None
        if deadline is not None:
            _sleep_until(deadline)


def _sleep_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
