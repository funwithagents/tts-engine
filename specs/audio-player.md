---
code:
  - src/tts_engine/audio.py
tests:
  - tests/test_audio.py
---

# AudioPlayer

**Status:** Implemented

## Purpose

`AudioPlayer` consumes raw PCM chunks produced by a `TTSModule` and writes them to the system audio output in real-time using `sounddevice`. It is the **default** `AudioSink` — the local-speaker implementation of the sink seam ([audio-sink.md](audio-sink.md)); an embedder can substitute its own destination without touching this class.

## Interface

`AudioPlayer` is declared to implement the `AudioSink` Protocol ([audio-sink.md](audio-sink.md)) — its `feed`/`drain` shape *is* the sink contract. This is a static-conformance declaration only; there is no behavior change.

```python
class AudioPlayer(AudioSink):
    def __init__(self, sample_rate: int, device: str | int | None = None) -> None:
        """
        Prepare the player. Does not open the audio stream yet.
        `sample_rate`: Hz to open the output stream at. Required — there is no
        meaningful default, since it must match the PCM the module feeds.
        Supplied by the `TTSEngine` constructor from the active module's
        `sample_rate` property (see tts-module-interface.md).
        `device`: sounddevice output device (None = system default).
        Supplied from `PlayerConfig.device` (the `engine.player` block) by
        the `TTSEngine` constructor; see configuration.md.
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
| Channels | 1 (mono) |
| Sample rate | The `sample_rate` passed to `__init__` (Hz), from the active module's declared rate |

Encoding and channel count are fixed constants; the sample rate is a required constructor argument, set once from the module's `sample_rate` and used to open the `OutputStream`. There is no default rate — it must match the PCM the module feeds. The player does not resample — a module must feed PCM already at the rate it declared.

## Implementation notes

### Lazy sounddevice import

`sounddevice` is imported **lazily inside `AudioPlayer`** (at the first `feed()`), not at `audio.py` module top level. This keeps `import tts_engine` and an engine driven by a custom sink working on hosts with no PortAudio / no audio device — see [audio-sink.md](audio-sink.md), "Importable without local audio." `numpy` remains a top-level import (no system dependency).

### Stream lifecycle

- Open a `sounddevice.OutputStream` on the first `feed()` call (lazy open), not in `__init__`. This avoids opening the device if synthesis fails before the first chunk, and is also where sounddevice is first imported.
- If opening succeeds but `stream.start()` fails, close the new stream before propagating the error.
- `drain()` detaches the active stream, calls `stream.stop()`, and always calls `stream.close()` in a `finally` block. A failed stop therefore cannot leak the device or leave a stale stream attached to the player.

### Buffering

`feed(chunk)` converts the bytes to a `numpy.ndarray` of `int16` and calls `sounddevice.OutputStream.write()` in blocking mode — no queue. This is the simplest option and works here because the module already drives `feed()` from a single `asyncio.to_thread` worker, so the blocking write never runs on the event loop.

(An alternative design would bridge `feed()` and a PortAudio callback thread through a `queue.Queue`, emitting silence when the queue is empty; that is not used.)

### System dependency

Requires `libportaudio2` on Ubuntu:

```bash
sudo apt-get install libportaudio2
```

`sounddevice` is a Python package (`pip install sounddevice`) that wraps PortAudio via cffi.
