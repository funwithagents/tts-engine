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
        Write a PCM chunk to the output stream.
        Opens the stream on the first call; subsequent calls write directly.
        Thread-safe: may be called from any thread (e.g. a PortAudio callback thread).
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

Use a `queue.Queue` to bridge between the `feed()` caller (async/main thread) and the PortAudio callback (audio thread):

1. `feed(chunk)` converts bytes to a `numpy.ndarray` of `int16` and puts it on the queue.
2. The `sounddevice` callback reads from the queue and fills the output buffer.
3. If the queue is empty, output silence (zeros) to avoid glitches.

Alternatively, use `sounddevice.OutputStream.write()` in blocking mode (simpler, no queue needed) — acceptable if it does not cause issues with the async event loop. Use `asyncio.to_thread` for the blocking write if needed.

### System dependency

Requires `libportaudio2` on Ubuntu:

```bash
sudo apt-get install libportaudio2
```

`sounddevice` is a Python package (`pip install sounddevice`) that wraps PortAudio via cffi.
