---
code:
  - src/tts_engine/modules/base.py
  - src/tts_engine/modules/__init__.py
tests:
  - tests/modules/test_tts_module_interface.py
---

# TTS Module Interface

**Status:** Implemented

## Purpose

The `TTSModule` ABC defines the contract every TTS backend must implement. It decouples the engine and server from any specific provider.

A module synthesizes from two input shapes: a **complete text** (`synthesize_text`) and a **text stream** (`synthesize_text_stream`) — pieces of text arriving over time, typically the tokens of an LLM response. Both feed the same PCM callback, chunk by chunk; the difference is only on the input side. The stream shape exists so audio can start while the text is still being produced, instead of waiting for the full response and then synthesizing it.

## Dataclasses

### `TTSOptions`

Carries per-call synthesis parameters. Currently empty — all synthesis options (voice, model, etc.) come from the module's config. Reserved for future per-call overrides (e.g. speed, language).

```python
@dataclass
class TTSOptions:
    pass
```

Module-specific configuration is not modeled as a dataclass: each module's constructor takes the raw module config `dict` (the `engine.module` block, see [configuration.md](configuration.md)) and validates it directly (see the ABC below).

## `TTSModule` ABC

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable


class TTSModule(ABC):
    def __init__(self, config: dict) -> None:
        """Construct from the module config block (the `engine.module` dict,
        including `type`). Subclasses validate and extract the fields they
        need, raising `ConfigError` for missing/invalid ones."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """The sample rate (Hz) of the PCM the module feeds to `callback`.

        Declared by the module and fixed for its lifetime — the engine reads it
        once and opens the `AudioPlayer` output stream at this rate (see the
        audio format contract below). A module must resample internally if its
        backend's native rate differs from the value returned here."""

    @abstractmethod
    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize the complete `text` and call `callback` with each PCM chunk
        as it arrives.

        - `text`: the string to synthesize. Must not be empty.
        - `options`: per-call overrides; currently empty and reserved for future use.
        - `callback`: called sequentially with each raw audio chunk. Calls may run
          off the event-loop thread but must never overlap.

        Raises `TTSError` on synthesis failure.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """

    @abstractmethod
    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize text as it arrives from `text_stream` and call `callback`
        with each PCM chunk as it is produced — audio for the first part of the
        text should play while later parts are still being received.

        - `text_stream`: an async iterator of text pieces, concatenated verbatim
          (no separator is inserted — an LLM token stream carries its own
          spaces). Empty pieces are skipped. An exhausted stream with no text
          produces no audio and is not an error.
        - `options`, `callback`: as for `synthesize_text`.

        Raises `TTSError` on synthesis failure. An exception raised by
        `text_stream` itself aborts the utterance and propagates unchanged.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """
```

Both methods are abstract: every module implements both. A backend whose API accepts text incrementally (a WebSocket that takes repeated text messages before an end-of-stream marker — Gradium, ElevenLabs, Deepgram) implements `synthesize_text_stream` as a true stream-to-stream pass: pieces go to the backend as they arrive and audio comes back while more text is still being received. A backend that needs a complete prompt before it can produce any audio (a local model conditioned on the whole text — pocket; a request/response API — mistral, gemini; a fixture matching whole sentences — audiofile) **groups the pieces into sentences** and synthesizes each sentence as soon as it is complete, so audio for sentence one plays while the stream is still delivering sentence two. The grouping is the module's own concern, done with the shared `segment_sentences` helper (below) — the engine never cuts text. Which shape a module uses is stated in its own spec.

## Module registry

Each backend is a **package of its own** under `src/tts_engine/modules/` — `elevenlabs/`, `pocket/`, `gradium/`, `phonon/`, `mistral/`, `deepgram/`, `gemini/`, `qwen3/`, `tone/`, `audiofile/` — never a loose file next to `base.py`. The `TTSModule` subclass lives in `<backend>/module.py` and the package `__init__.py` re-exports it, so `from tts_engine.modules.<backend> import <Class>` is the import path everywhere (registry, tests, callers). Each backend is specced by `specs/modules/<backend>.md`.

`modules/__init__.py` maintains a `REGISTRY` dict mapping type strings to module classes:

```python
REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,  # provider, behind the `elevenlabs` extra
    "pocket": PocketModule,  # provider, behind the `pocket` extra
    "phonon": PhononModule,  # provider, behind the `phonon` extra
    "gradium": GradiumModule,  # provider, behind the `gradium` extra
    "mistral": MistralModule,  # provider, behind the `mistral` extra
    "deepgram": DeepgramModule,  # provider, behind the `deepgram` extra
    "gemini": GeminiModule,  # provider, behind the `gemini` extra
    "qwen3": Qwen3Module,  # provider, behind the `qwen3` extra
    "tone": ToneModule,  # fixture, base install
    "audiofile": AudioFileModule,  # fixture, base install
}
```

`load_module(tts_config: dict) -> TTSModule` reads `tts_config["type"]`, looks it up in `REGISTRY`, and constructs the module with the complete config dictionary, including `type` (and `base_dir` when the loader filled it in — see [configuration.md](configuration.md)). Raises `ConfigError` for unknown types. The registry always lists every module, installed extra or not: selecting a provider whose extra is absent fails at *construction* with an actionable `ConfigError` (see "Dependencies" below), never at import.

There is **no default module**. `type` is required, and no module is privileged in code, docs, or examples.

## Module kinds

| Kind | Modules | Installed by | Purpose |
|---|---|---|---|
| **Provider** (API-backed) | `elevenlabs`, `gradium`, `mistral`, `deepgram`, `gemini` | the `elevenlabs` / `gradium` / `mistral` / `deepgram` / `gemini` extra | Real speech from a cloud API |
| **Provider** (local model) | `pocket`, `phonon`, `qwen3` | the `pocket` / `phonon` / `qwen3` extra | Real speech from an in-process model |
| **Fixture** | `tone`, `audiofile` | base install | No synthesis; deterministic audio for tests, demos, and downstream integration suites |

Fixture modules implement the full contract (declared rate, PCM format, cancellation) and are first-class members of the registry, but the documentation must never present them as TTS: users wanting speech install a provider extra. See [tone-module.md](modules/tone.md) and [audiofile-module.md](modules/audiofile.md).

## Path resolution

Some module fields name files (an `audiofile` WAV, a pocket voice `.wav`). Relative paths are meant to be relative to the config file, and the config loaders record that directory in the reserved `base_dir` module key ([configuration.md](configuration.md), "`engine.module` block"). `modules/base.py` exposes the one shared rule:

```python
def resolve_path(config: dict, value: str) -> Path:
    """Resolve a file path from a module config field.

    Absolute paths are returned as-is. A relative path is joined onto
    ``config["base_dir"]`` when present, else onto the current working
    directory. This is a locate only — the file is not opened or checked.
    """
```

Every module resolves every path field through it, so no module re-implements the rule.

## Audio format contract

All modules **must** feed `callback` raw PCM in this format:

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit PCM (little-endian) |
| Channels | 1 (mono) |
| Sample rate | Declared by the module via the `sample_rate` property (Hz) |

Encoding and channel count are fixed. **Sample rate is module-declared**: each module returns its rate from `sample_rate`, and the engine opens the `AudioPlayer` output stream at that rate (see [audio-player.md](audio-player.md) and [architecture.md](architecture.md)). This lets API providers that stream at 44100 Hz (ElevenLabs) and local models with a native rate of, say, 24000 Hz coexist without a project-wide resample. A module whose backend emits a different rate than it declares must resample internally before the callback.

Modules must not emit MP3 or other encoded formats without an explicit decoding step, and must not emit float samples — convert to signed 16-bit before the callback.

## Cancellation

Neither `synthesize_text()` nor `synthesize_text_stream()` may return or raise while its worker thread can still call `callback` — otherwise the engine would `drain()` the sink (closing the player) while chunks are still arriving, and release its `say` lock while an old utterance is still playing. A running `asyncio.to_thread` worker cannot be cancelled from outside, so cancellation is **cooperative**: every module runs its blocking loop through `run_cancellable_worker(worker)` in `modules/base.py` (or, for the stream shape, `run_text_stream_worker`, which wraps it — see "Text-stream helpers"). The helper hands the worker a `threading.Event` (`stop`); the worker polls `stop.is_set()` between chunks and returns as soon as it is set. On `CancelledError` the helper sets the flag, waits for the thread to exit, and only then re-raises. A module written this way satisfies the "returns or raises only after it has stopped invoking `callback`" clause of the ABC docstring for free.

## Text-stream helpers

Two helpers in `modules/base.py` keep the `synthesize_text_stream` implementations short, the same way `run_cancellable_worker` is shared. Neither is forced on a module: a backend with a native incremental API uses the bridge and not the segmenter; a sentence-grouping backend uses both.

### `run_text_stream_worker` — the async-source → worker-thread bridge

Modules do their blocking work in a worker thread, but the text stream is an async iterator on the **engine's** event loop (an LLM SDK stream). The bridge pumps one into the other:

```python
async def run_text_stream_worker(
    text_stream: AsyncIterator[str],
    worker: Callable[[Iterator[str], threading.Event], None],
) -> None:
    """Run a blocking ``worker(pieces, stop)`` in a thread, fed from an async
    text stream, with cooperative cancellation (see ``run_cancellable_worker``).

    ``pieces`` is a blocking iterator over the stream's text pieces: ``next``
    waits for the next piece, ends at end-of-stream, and ends early when
    ``stop`` is set (so a worker blocked on text still exits promptly). Empty
    pieces are dropped before they reach the worker.
    """
```

- A **pump task** on the calling loop does `async for piece in text_stream` and hands each piece to a thread-safe `queue.Queue`, then an end-of-stream sentinel. The worker runs through `run_cancellable_worker`; its `pieces` iterator blocks on the queue, waking every 50 ms to poll `stop`.
- **Source failure aborts.** If `text_stream` raises, the pump records the exception and sets `stop`; the worker exits, and once its thread has stopped the helper raises the source's exception **unchanged** — it is the caller's error (the LLM failed), never relabeled as `TTSError`. Audio already fed is not taken back; nothing further is fed.
- **Worker failure** (a `TTSError`, or a callback failure) cancels the pump — the source is closed early rather than consumed to the end — and propagates unchanged. A worker that returns normally before the stream is exhausted also cancels the pump.
- **Cancellation** of the awaiting coroutine cancels the pump, sets `stop`, waits for the thread, and re-raises `CancelledError` — exactly `run_cancellable_worker`'s rule, so no callback can occur after the coroutine exits.
- A module that opens a private event loop in its worker (the async-SDK shape) drives `next(pieces)` through `run_in_executor` so that loop keeps receiving audio while it waits for text.

### `split_at_last_space` — word-boundary grouping for incremental backends

```python
def split_at_last_space(buffer: str) -> tuple[str, str]:
    """Split `buffer` after its last whitespace: (complete words, the rest)."""
```

A backend that takes text incrementally (Gradium, ElevenLabs) forwards only up to the last whitespace of what it has buffered, holding back a possibly unfinished last word for the next piece or the end of stream, so a sub-word token never reaches the server alone — whichever side the tokenizer puts its spaces on.

### `segment_sentences` — sentence grouping for non-incremental backends

```python
def segment_sentences(pieces: Iterable[str], *, max_chars: int = 200) -> Iterator[str]:
    """Group text pieces into sentences, yielding each as soon as it is complete."""
```

A pure, lazy generator — it works equally on an in-memory list and on the bridge's blocking iterator, yielding as pieces arrive rather than at the end:

- Pieces are concatenated verbatim into a buffer. A sentence is yielded (stripped) when the buffer holds a **sentence end**: `.`, `!`, `?` or `…` — optionally followed by a closing quote or bracket — **followed by whitespace**, or a newline. "Followed by whitespace" is what keeps `3.14` and `v2.5` whole; it also means `Hello.` is yielded when the next piece starts (or at end of input), not the instant the period arrives.
- **Length cap.** Once the buffer exceeds `max_chars` with no sentence end, it is cut at the last **clause boundary** (`,`, `;`, `:` followed by whitespace), else at the last whitespace — never inside a word — so a long LLM sentence cannot stall playback. `max_chars` is a fixed default in v1 (not configurable); modules pass nothing.
- At end of input the remainder is yielded if it is not blank. Blank segments are never yielded.
- Known limitation, accepted: abbreviations such as `Dr. Smith` split as a sentence. The backends that use this (pocket, phonon, qwen3, mistral, gemini, audiofile) tolerate a short segment; correctness of the audio is unaffected.

## Local-model modules

The API-backed pattern (ElevenLabs) is one shape; a second shape is a **local-model module** that runs inference in-process (e.g. Kokoro, ChatTTS, Piper). The [`huggingface/speech-to-speech`](https://github.com/huggingface/speech-to-speech/tree/main/src/speech_to_speech/TTS) TTS handlers are a useful catalogue of which engines work well and how to invoke them, but they are **not** reusable as-is: each extends that project's `BaseHandler[TTSIn, TTSOut]` pipeline (a `setup()`/queue/`CancelScope` framework), yields `numpy` arrays at 16 kHz, and pulls in the whole app package. A local-model module here wraps the **underlying model library directly** (e.g. the `kokoro` package), not the handler, and honors this interface:

- **Config-driven construction.** `__init__` validates the `engine.module` block and loads the model (or defers the load to first use). Model download / device selection are config fields, mirroring `api_key_env`/`voice_id` on ElevenLabs.
- **`sample_rate` reports the module's native rate** (commonly 24000 for these models), so no resampling is needed unless the backend itself varies.
- **Inference runs off the event loop.** Local inference is blocking and CPU/GPU-bound; run it inside `asyncio.to_thread` (as ElevenLabs does for its blocking SDK iterator), driving `callback` from that single worker thread so calls never overlap.
- **Float → int16 conversion** happens in the module before the callback (clip to ±1.0, scale to the int16 range), since these libraries yield float waveforms.
- **Cancellation and errors** follow the same rules as any module: stop calling `callback` before returning/raising, and wrap backend/inference failures in `TTSError`. When the library accepts a stop signal of its own (pocket-tts's `stop` event, Phonon's stream `close()`, closing the qwentts.cpp generator), the worker hands it the cancellation so inference stops too, not just the callbacks.
- **Text stream = sentence grouping.** A local model conditions on its whole prompt before emitting a frame, so there is no token-in/audio-out mode to call. `synthesize_text_stream` runs one worker through `run_text_stream_worker`, groups the pieces with `segment_sentences`, and runs inference per sentence inside that single worker — audio for one sentence plays while the stream is still delivering the next. The reference is [pocket-module.md](modules/pocket.md).

Concrete config fields and dependencies for a specific local-model module are specced alongside its code when it lands, following this contract.

## Async-SDK modules

A third shape is an API-backed module whose SDK is **async-only** (an aiohttp/websockets client with `async for` chunk iteration, e.g. Gradium). It cannot simply drive `callback` from the engine's event loop: the default sink's `feed` blocks on the sound device, which would stall every other coroutine for the length of the playback. Such a module therefore runs the same cancellable worker as everyone else and opens a **private event loop inside it** — `asyncio.run(consume(...))` as the body handed to `run_cancellable_worker` — consuming the SDK stream and invoking `callback` from that thread. The consumer polls `stop` between chunks and leaves the SDK's context manager on cancel so the connection closes promptly. For the text-stream shape the worker comes from `run_text_stream_worker` instead, and the private loop runs a **sender** (pulling `next(pieces)` through `run_in_executor`, forwarding text to the socket, then end-of-stream) concurrently with the **receiver** (the audio loop above) — true stream to stream over one connection. The reference implementation is [gradium-module.md](modules/gradium.md), "Threading model".

## Dependencies

Every **provider** module — API-backed or local-model — ships behind its own packaging **extra** and imports its library lazily inside `__init__` (never at the top of `<backend>/module.py` or its package `__init__.py`, because `modules/__init__.py` imports every module class to build the registry). A missing extra becomes `ConfigError("The '<type>' module requires the <type> extra: pip install tts-engine[<type>]")` at construction. Fixture modules live in the base install and need nothing beyond the standard library and `numpy`. See [project.md](project.md), "Dependency strategy for TTS backends".

## Error handling

- Modules raise `TTSError` (defined in `modules/base.py`) for provider and decoding failures (API errors, network errors, invalid responses).
- `ConfigError` is raised in the constructor for invalid or missing config fields.
- Callback failures belong to the downstream consumer and must propagate unchanged rather than being mislabeled as provider failures.
- Text-stream failures (the async source raising) belong to the caller and propagate unchanged; `run_text_stream_worker` enforces this.
- Modules must not swallow exceptions silently.

## Open questions

Deferrals only (design is settled):

1. **Configurable `max_chars`** for `segment_sentences`. A fixed default serves the known backends; a per-module config field (or a `TTSOptions` field) can be added without changing the helper's shape if a backend wants shorter or longer segments.
