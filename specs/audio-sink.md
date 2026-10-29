---
code:
  - src/tts_engine/audio.py
  - src/tts_engine/engine.py
  - src/tts_engine/__init__.py
tests:
  - tests/test_audio.py
  - tests/test_engine.py
  - tests/test_no_audio_import.py
---

# Audio Sink

**Status:** Implemented

## Purpose

`TTSEngine.say` (and `say_stream`, its text-stream counterpart) synthesizes PCM and, by default, plays it on the local machine through `AudioPlayer` (sounddevice). Some embedders need that PCM to go **somewhere other than the local speaker** — a robot's audio pipeline, an in-memory buffer, a network stream — without reimplementing `say`.

The `AudioSink` Protocol is the seam that makes the playback destination swappable. It names the `feed`/`drain` shape `AudioPlayer` already has, so an embedder can supply its own destination and capture the synthesized stream through the engine's **public** API instead of reaching into module internals.

This is a generic seam, not a robot-specific one: it generalizes to "let any embedder capture the synthesized stream instead of playing it locally." Resampling and format conversion are the sink's concern, not the engine's — see [tts-module-interface.md](tts-module-interface.md) for the fixed PCM contract the engine always emits.

## Core concepts / Decided

### The `AudioSink` Protocol

```python
from typing import Protocol


class AudioSink(Protocol):
    """Destination for synthesized PCM chunks.

    Chunks are raw signed-16-bit little-endian **mono** PCM at the active
    module's declared `sample_rate` (the same bytes AudioPlayer.feed receives).
    A sink that needs a different rate or dtype converts internally.
    """

    def feed(self, chunk: bytes) -> None: ...
    def drain(self) -> None: ...
```

- Lives in [audio.py](../src/tts_engine/audio.py) alongside `AudioPlayer`.
- **Exported** from the package top level: `from tts_engine import AudioSink`. It is the seam embedders type their own sink against (added to `__init__.py`'s `__all__`).

### PCM contract fed to a sink

Identical to the module→callback contract ([tts-module-interface.md](tts-module-interface.md)) and to what `AudioPlayer` consumes ([audio-player.md](audio-player.md)):

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit PCM (little-endian) |
| Channels | 1 (mono) |
| Sample rate | The active module's declared `sample_rate` (Hz) |

The engine does **not** resample or reformat. A sink whose destination wants another rate/dtype (e.g. float32 at 48 kHz) converts internally, and reads the source rate from `TTSEngine.sample_rate` (below). The one change the engine may make is to the sample *values*: a non-zero `gain_db` scales them before `feed`, in the same format, at the same rate and in the same chunks ([architecture.md](architecture.md), "Output gain"). At the default `0` the sink receives the module's bytes unchanged.

### Call discipline

The same guarantees `TTSModule.synthesize_text` / `synthesize_text_stream` document for their callback carry through to the sink:

- `feed` is called sequentially with each chunk. Calls may run off the event-loop thread (the module drives them from a single `asyncio.to_thread` worker) but **never overlap**.
- `drain` is called **exactly once** after the last `feed`, at the end of `say` (or `say_stream`) — including when synthesis fails, the text stream fails, or the coroutine is cancelled. Both methods wrap the synthesis in the shared `cleanup` helper so `drain` always runs on the active sink. A `say_stream` utterance is **one** utterance to the sink however many sentences or pieces it was built from: a single `drain` at the end, so the device stays open across sentence boundaries with no click between them.
- **Thread affinity.** `feed` is called from the module's worker thread. `drain` is called **synchronously on the engine's event loop**, by the coroutine that owns the utterance, once the worker has exited. A drain that blocks (the default player waits for its buffered audio to play out and closes the stream) therefore blocks the loop for that long; a custom sink whose drain is slow should do its waiting elsewhere or accept that cost. This is a documented limitation, not a promise that the loop never waits on the sink.
- **A failing `drain` never hides the utterance's outcome.** If synthesis raised, the callback raised, the source raised or the call was cancelled, that exception propagates and a `drain` failure is logged at WARNING; if the utterance completed, a `drain` failure propagates (it is then the only error). See [tts-module-interface.md](tts-module-interface.md), "`cleanup`".
- Concurrency across calls is unchanged: `TTSEngine` serializes all `say` and `say_stream` calls under its `asyncio.Lock`, so a sink never sees two overlapping utterances.

### `AudioPlayer` is an `AudioSink`

`AudioPlayer` already has this exact `feed`/`drain` shape. It is declared to implement `AudioSink` (explicit conformance, statically checked) with **no behavior change**.

### Constructor injection

A custom sink is supplied at construction; the engine uses it for the lifetime of the instance.

```python
class TTSEngine:
    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        self._module = load_module(config.module)
        self._sink: AudioSink = (
            sink
            if sink is not None
            else AudioPlayer(
                device=config.player.device, sample_rate=self._module.sample_rate
            )
        )
        self._say_lock = asyncio.Lock()

    async def say(self, text: str) -> None:
        async with self._say_lock:
            try:
                await self._module.synthesize_text(
                    text, TTSOptions(), callback=self._sink.feed
                )
            finally:
                self._sink.drain()

    async def say_stream(self, text_stream: AsyncIterable[str] | Iterable[str]) -> None:
        async with self._say_lock:
            try:
                await self._module.synthesize_text_stream(
                    _as_async_iterator(text_stream), TTSOptions(), callback=self._sink.feed
                )
            finally:
                self._sink.drain()
```

- **Additive and backwards compatible.** With no `sink`, behavior is identical to today: the engine builds an `AudioPlayer` from `config.player` at the module's `sample_rate`.
- **No per-call sink override.** `say(text)` keeps its single-argument signature. The destination is fixed per engine instance. (A per-call override was considered and deferred — see Open questions.)
- When a custom `sink` is injected, the engine **does not construct an `AudioPlayer`** and never touches sounddevice.
- **The engine does not own the sink's lifetime.** `TTSEngine.close()` releases the module and leaves the sink alone: an injected sink belongs to the embedder, who closes whatever it holds; the default `AudioPlayer` holds nothing between utterances (its stream opens on the first `feed` and closes in `drain`), so there is nothing to close. The one sink call per utterance, `drain`, is the whole lifecycle the engine drives ([architecture.md](architecture.md), "Ownership and lifetime").

### `TTSEngine.sample_rate`

A read-only property exposing the active module's rate, so a sink can resample from it:

```python
    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) of the PCM chunks fed to the sink — the active
        module's declared rate. Fixed for the engine's lifetime."""
        return self._module.sample_rate
```

### Importable without local audio

The engine (and `import tts_engine`) must work on hosts with **no PortAudio / no audio device**, so an embedder that only ever uses a custom sink is not forced to install or load sounddevice.

- `sounddevice` is imported **lazily**, inside `AudioPlayer` on the first `feed()` — not at module top level. So `import tts_engine`, `TTSEngine(config, sink=custom_sink)`, and `say` through that sink never import sounddevice.
- `numpy` stays a top-level import in `audio.py` — it has no system dependency and is needed by `AudioPlayer`'s buffer conversion.
- The default (no-sink) path is unchanged for a host that *does* have audio: the first `feed` imports sounddevice and opens the stream lazily, exactly as [audio-player.md](audio-player.md) already specifies.
- Guarded by `tests/test_no_audio_import.py`, which runs the import → construct-with-sink → `say` path in a subprocess where `sounddevice` is made unimportable.

## Open questions

- **Per-call sink override** (`say(text, *, sink=...)`) — deferred. Constructor injection covers the known embedder (the sink is available when the engine is constructed). A per-call override would only earn its place if one engine must route different utterances to different destinations, or if the sink is a runtime handle unavailable at construction time. Purely additive, so it can be added later without breaking the constructor-injection contract.
