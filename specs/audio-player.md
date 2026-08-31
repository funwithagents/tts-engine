---
code:
  - src/tts_mcp/audio.py
tests:
  - tests/test_audio.py
---

# AudioPlayer

**Status:** Implemented

## Purpose

`AudioPlayer` consumes raw PCM chunks produced by a `TTSModule` and writes them to the system audio output in real-time using `sounddevice`.

## Interface

```python
class AudioPlayer:
    def __init__(self, device: str | int | None = None) -> None:
        """
        Prepare the player. Does not open the audio stream yet.
        `device`: sounddevice output device (None = system default).
        """

    def feed(self, chunk: bytes) -> None:
        """
        Write a PCM chunk to the output stream (blocking write).
        Opens the stream on the first call; subsequent calls write directly.
        Called from a single module worker thread at a time (not concurrently).
        """

    def drain(self) -> None:
        """
        Block until all buffered audio has been played, then close the stream.
        Called by TTSEngine after the module signals end-of-stream.
        """
```

## Audio format

Matches the module contract (see `tts-module-interface.md`):

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit PCM (little-endian) — `dtype='int16'` in sounddevice |
| Sample rate | 44100 Hz |
| Channels | 1 (mono) |

## Implementation notes

### Stream lifecycle

- Open a `sounddevice.OutputStream` on the first `feed()` call (lazy open), not in `__init__`. This avoids opening the device if synthesis fails before the first chunk.
- `drain()` calls `stream.stop()` then `stream.close()`.

### Buffering

`feed(chunk)` converts the bytes to a `numpy.ndarray` of `int16` and calls `sounddevice.OutputStream.write()` in blocking mode — no queue. This is the simplest option and works here because the module already drives `feed()` from a single `asyncio.to_thread` worker, so the blocking write never runs on the event loop.

(An alternative design would bridge `feed()` and a PortAudio callback thread through a `queue.Queue`, emitting silence when the queue is empty; that is not used.)

### System dependency

Requires `libportaudio2` on Ubuntu:

```bash
sudo apt-get install libportaudio2
```

`sounddevice` is a Python package (`pip install sounddevice`) that wraps PortAudio via cffi.
