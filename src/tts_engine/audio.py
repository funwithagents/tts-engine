"""AudioPlayer: sounddevice-based streaming playback."""
import logging

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

_SAMPLERATE = 44100
_CHANNELS = 1
_DTYPE = "int16"


class AudioPlayer:
    def __init__(self, device: str | int | None = None) -> None:
        self._device = device
        self._stream: sd.OutputStream | None = None

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=_SAMPLERATE,
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
