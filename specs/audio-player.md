---
code:
  - src/tts_engine/audio.py
tests:
  - tests/test_audio.py
  - tests/test_no_audio_import.py
  - tests-e2e/test_discard.py
---

# AudioPlayer (and DiscardPlayer)

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

## `DiscardPlayer`

The player for **no device**: the second built-in `AudioSink` in `audio.py`, which the engine builds instead of `AudioPlayer` when `engine.player.discard` is `true` ([configuration.md](configuration.md), "`engine.player` block"). It exists for a host whose only audio device must not hear the voice (a CI runner whose one virtual cable feeds the application's microphone): the voice goes nowhere, yet takes its time.

```python
class DiscardPlayer(AudioSink):
    def __init__(self, sample_rate: int) -> None: ...
    def feed(self, chunk: bytes) -> None: ...
    def drain(self) -> None: ...
```

- **No device.** It opens nothing and never imports sounddevice, so it works on a host with no PortAudio, like an injected sink ([audio-sink.md](audio-sink.md), "Importable without local audio").
- **Real-time pace.** It keeps `AudioPlayer`'s timing, which callers rely on: an utterance lasts as long as its audio, "speaking" ends when the audio is out, and a cancel lands mid-speech. `feed(chunk)` blocks as a blocking device write does: until the audio fed so far is at most `DISCARD_BUFFER_SECONDS` (0.1 s, the stand-in for a device's output buffer) ahead of real time.
- **One running deadline.** The pace runs against a deadline — the utterance's first `feed` time plus the duration of everything fed since (bytes / 2 / `sample_rate`) — not by sleeping each chunk's duration, so per-call overhead never accumulates. A producer slower than real time is an underrun, as on a device: the deadline restarts from the time of the late chunk. An empty chunk is ignored.
- **`drain`** waits until the deadline (the buffered remainder "plays out", as `AudioPlayer.drain`'s `stream.stop()` plays out what was written), then resets, so the next utterance is paced from its own start. A drain with nothing fed returns at once.
- **Tested** on a virtual clock in the fast tier (`time` patched in `audio.py`, the pace asserted exactly, an engine built from a discard config with sounddevice unimportable), and once on the real clock in the live tier: phonon through a discard config, `say` as long as its audio, no stream opened ([testing.md](testing.md), `test_discard.py`).
- **Cancellation** matches `AudioPlayer`: the module stops feeding between slices, and the engine's single `drain` then waits out what was already fed — at most one buffer plus the chunk in flight — before the cancel propagates.
