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

    def close(self) -> None:
        """Release what the module holds for its lifetime: a provider closes
        its SDK client, a local model drops its weights. Called once, by
        `TTSEngine.close()`, with no utterance in flight; the module must not
        be used afterwards. The default releases nothing."""
```

Both methods are abstract: every module implements both. A backend whose API accepts text incrementally (a WebSocket that takes repeated text messages before an end-of-stream marker — Gradium, ElevenLabs, Deepgram, Cartesia) implements `synthesize_text_stream` as a true stream-to-stream pass: pieces go to the backend as they arrive and audio comes back while more text is still being received. A backend that needs a complete prompt before it can produce any audio (a local model conditioned on the whole text — pocket; a request/response API — mistral, gemini, openai; a fixture matching whole sentences — audiofile) **groups the pieces into sentences** and synthesizes each sentence as soon as it is complete, so audio for sentence one plays while the stream is still delivering sentence two. The grouping is the module's own concern, done with the shared `segment_sentences` helper (below) — the engine never cuts text. Which shape a module uses is stated in its own spec.

## Lifetime and ownership

Who constructs, who releases, and when:

- **The engine owns the module.** `TTSEngine.__init__` builds it through `load_module`; `TTSEngine.close()` calls its `close()` once, with no utterance in flight. A module holds for its lifetime what it builds in `__init__` — an SDK client with its HTTP connection pool (elevenlabs, gradium, mistral, deepgram, gemini, cartesia, openai) or a loaded model (pocket, phonon, qwen3, kokoro, piper) — and that is what `close()` releases: the clients that have a close call get it (`OpenAI.close`, `genai.Client.close`, `Cartesia.close`, Mistral's context exit), a local model is dropped so its weights can be collected (qwen3 also frees its native context). ElevenLabs, Gradium and Deepgram clients expose no close; they hold only a lazily opened pool that goes with the module. Fixtures release nothing. Each module's spec has a "Lifetime" line saying which applies.
- **An utterance owns its transport.** Sockets, HTTP responses, decoders and generators are opened by `synthesize_text` / `synthesize_text_stream` and released before they return or raise, through `cleanup` (below), on every path. Nothing of an utterance survives it except the threads below.
- **Threads that outlive an utterance** hold nothing of the module's and are never waited for: a socket's closing tail (deepgram and cartesia close off the worker after an abort, since the server can hold the handshake for seconds), a lookahead producer finishing one uninterruptible inference (kokoro, piper), an abandoned connection setup (`open_cancellable`, below). All are daemon threads; `close()` does not join them. Their completion is best effort and must not be mistaken for a guarantee.
- **The engine owns the text source for one utterance.** `say_stream` consumes the iterator it was given and, once the utterance is over on any path, awaits the iterator's `aclose()` when it has one (an async generator's `finally` runs then; an SDK stream object is closed), under the `cleanup` precedence rule: after a failure a failing `aclose` is only logged. The bridge also cancels its pump early when the worker fails or returns first, so the source is not read to the end for nothing ([architecture.md](architecture.md), "Ownership and lifetime").
- **The sink is the embedder's.** An injected `AudioSink` is neither constructed nor closed by the engine; the default `AudioPlayer` holds nothing between utterances (its stream opens on the first `feed` and closes in `drain`). See [audio-sink.md](audio-sink.md).

## Module registry

Each backend is a **package of its own** under `src/tts_engine/modules/` — `elevenlabs/`, `pocket/`, `gradium/`, `phonon/`, `mistral/`, `deepgram/`, `gemini/`, `cartesia/`, `openai/`, `qwen3/`, `kokoro/`, `piper/`, `tone/`, `audiofile/` — never a loose file next to `base.py`. The `TTSModule` subclass lives in `<backend>/module.py` and the package `__init__.py` re-exports it, so `from tts_engine.modules.<backend> import <Class>` is the import path everywhere (registry, tests, callers). Each backend is specced by `specs/modules/<backend>.md`.

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
    "cartesia": CartesiaModule,  # provider, behind the `cartesia` extra
    "openai": OpenAIModule,  # provider, behind the `openai` extra
    "qwen3": Qwen3Module,  # provider, behind the `qwen3` extra
    "kokoro": KokoroModule,  # provider, behind the `kokoro` extra
    "piper": PiperModule,  # provider, behind the `piper` extra
    "tone": ToneModule,  # fixture, base install
    "audiofile": AudioFileModule,  # fixture, base install
}
```

`load_module(tts_config: dict) -> TTSModule` reads `tts_config["type"]`, looks it up in `REGISTRY`, and constructs the module with the complete config dictionary, including `type` (and `base_dir` when the loader filled it in — see [configuration.md](configuration.md)). Raises `ConfigError` for unknown types. The registry always lists every module, installed extra or not: selecting a provider whose extra is absent fails at *construction* with an actionable `ConfigError` (see "Dependencies" below), never at import.

There is **no default module**. `type` is required, and no module is privileged in code, docs, or examples.

## Module kinds

| Kind | Modules | Installed by | Purpose |
|---|---|---|---|
| **Provider** (API-backed) | `elevenlabs`, `gradium`, `mistral`, `deepgram`, `gemini`, `cartesia`, `openai` | the `elevenlabs` / `gradium` / `mistral` / `deepgram` / `gemini` / `cartesia` / `openai` extra | Real speech from a cloud API |
| **Provider** (local model) | `pocket`, `phonon`, `qwen3`, `kokoro`, `piper` | the `pocket` / `phonon` / `qwen3` / `kokoro` / `piper` extra | Real speech from an in-process model |
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

Neither `synthesize_text()` nor `synthesize_text_stream()` may return or raise while its worker thread can still call `callback` — otherwise the engine would `drain()` the sink (closing the player) while chunks are still arriving, and release its `say` lock while an old utterance is still playing. A running `asyncio.to_thread` worker cannot be cancelled from outside, so cancellation is **cooperative**: every module runs its blocking loop through `run_cancellable_worker(worker)` in `modules/base.py` (or, for the stream shape, `run_text_stream_worker`, which wraps it — see "Text-stream helpers"). The helper hands the worker a `threading.Event` (`stop`); the worker polls `stop.is_set()` between chunks and returns as soon as it is set. On `CancelledError` the helper sets the flag, waits for the thread to exit, and only then re-raises. That wait survives further cancellation: a second `cancel()` arriving while the helper is already waiting is remembered and re-raised once the thread has exited, never allowed to cut the wait short (otherwise the engine would drain and release its lock with the worker still feeding). A module written this way satisfies the "returns or raises only after it has stopped invoking `callback`" clause of the ABC docstring for free.

### What cancellation guarantees, and what it does not

Three milestones are distinct, and only the first is the interface's promise:

1. **Callback quiescence** — no `callback` after the coroutine returns or raises. Every module guarantees it (above); it is what the engine's drain and lock rely on.
2. **Backend work stopped** — inference halted, the request dropped. Each module's spec says what it does here: a library with a stop signal is handed `stop` (pocket, phonon, qwen3), a socket is closed (gradium, elevenlabs, deepgram, cartesia), an HTTP response is closed (mistral, gemini, openai). Where the backend cannot be interrupted (kokoro-onnx, piper-tts), one sentence's inference finishes in the background and is discarded. A module whose runtime is **not thread-safe** must reach a point where that runtime is idle before returning, since the engine may start the next utterance on the same model immediately (pocket drains its generator so the library joins its decoder thread; see [pocket.md](modules/pocket.md)).
3. **Transport closed** — the socket's close handshake done. Deepgram and Cartesia finish it off the worker thread after an abort, since the server can hold it for seconds; nothing waits for it.

**Latency.** The worker sees `stop` only between the operations it polls around. Between chunks that is one chunk's playback (modules feeding big chunks slice them to 100 ms). While the worker is **blocked inside a network call** — opening a connection, waiting for the next byte of a stalled response — it cannot see `stop` until that call returns, so every API-backed module bounds such calls with a `timeout` config field (default 30 s) or its own poll: a stalled request becomes a `TTSError` after at most that long, and a cancel issued during a stall is honored by then. Modules whose receive loop polls (gradium, elevenlabs stream-input, deepgram, cartesia) honor a cancel within 100 ms while the server is silent, and their connection *setup* polls `stop` too, bounded by `timeout` (gradium on its private loop; deepgram and cartesia through `open_cancellable`, below), so a cancel during a handshake surfaces within 100 ms and the late connection is closed behind it. None of this is a contractual bound on cancellation latency; each module's spec states what it measured. Every provider that receives a byte stream (openai, mistral, gemini, deepgram, cartesia) feeds it through `PCMFeeder` ("Shared PCM helpers", below) in 100 ms slices, so none depends on the chunk sizes its provider happens to send.

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
- **The bridge is bounded.** The pump holds at most `MAX_PENDING_CHARS` (4096) characters the worker has not taken yet; past that it awaits room (an `asyncio.Event` the worker side sets, through `call_soon_threadsafe`, each time it takes a piece) instead of reading further. So the source sees **backpressure** at the worker's pace — an LLM stream is read as fast as it is spoken, not buffered without limit in memory — and a source that yields without ever suspending (an in-memory sequence, a pre-buffered SDK stream) cannot run the loop without a turn: it yields once the bound is reached, which is also what lets a cancel in. One piece larger than the bound still goes through once the queue is empty (progress is guaranteed; a count of pieces would not bound memory, so the bound is in characters). Empty pieces never reach the queue, so the pump yields the loop (`sleep(0)`) on each of them instead; a source yielding only empty pieces is cancellable too. The bound is a keyword-only parameter of the helper for tests; modules pass nothing.
- **Source failure aborts.** If `text_stream` raises, the pump records the exception and sets `stop`; the worker exits, and once its thread has stopped the helper raises the source's exception **unchanged** — it is the caller's error (the LLM failed), never relabeled as `TTSError`. Audio already fed is not taken back; nothing further is fed. A `CancelledError` raised by the source itself counts as a source failure too (told apart from the bridge's own cancel of its pump by `Task.cancelling()`): it aborts and propagates rather than passing for a complete, successful stream.
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
- **Length bound.** `max_chars` is a real bound on segment length, not a target, and it holds however the input is chunked (a complete text in one piece, or a token stream). A sentence end counts only if it falls within the first `max_chars` characters of the buffer. Once the buffer is longer than `max_chars` with no sentence end in that window, it is cut **inside the window** at the last **clause boundary** (`,`, `;`, `:` followed by whitespace), else at the last whitespace — never inside a word — so a long LLM sentence cannot stall playback and a long prompt is not handed to a backend whole. The one exception is a **single word longer than `max_chars`** (a URL, a hash): it is cut at the first whitespace after it and emitted as one over-long segment. `max_chars` is a fixed default in v1 (not configurable); modules pass nothing. (The first version cut at the last boundary anywhere in the buffer, so a 10 000-character piece produced a 10 000-character segment — bounded only when the pieces were short.)
- At end of input the remainder is yielded if it is not blank. Blank segments are never yielded.
- Known limitation, accepted: abbreviations such as `Dr. Smith` split as a sentence.

### `feed_with_lookahead` — whole-sentence backends

```python
def feed_with_lookahead(
    sentences: Iterable[str],
    synthesize: Callable[[str], Iterable[bytes]],
    callback: Callable[[bytes], None],
    stop: threading.Event,
    *,
    slice_bytes: int,
) -> None:
    """Synthesize `sentences` one step ahead of playback and feed the PCM in slices."""
```

Some local runtimes return a sentence's audio **all at once** rather than frame by frame (kokoro-onnx's `create`, Piper's per-sentence `AudioChunk`). Fed directly, two problems follow: the default sink's `feed` blocks for the whole chunk, so `stop` would not be seen until the sentence has played; and the next sentence would only be synthesized after the previous one finished playing, leaving a silence of one inference between sentences. This helper, called from inside a module's worker, solves both:

- A **producer thread** iterates `sentences`, calls `synthesize(sentence)` and puts each PCM chunk it yields on a queue. The calling (worker) thread takes chunks and feeds them to `callback` in slices of at most `slice_bytes` (modules pass 100 ms), checking `stop` before each slice. `callback` is only ever called from the worker thread, and the producer never calls it.
- The producer stays **at most one chunk ahead** of the chunk being played: it takes a single slot (a `threading.Semaphore(1)`) before synthesizing each chunk, and the worker gives the slot back when it takes a chunk to play. While sentence one plays, sentence two is synthesized and waits; sentence three starts only once sentence two starts playing. (A one-slot queue alone would allow two ahead: one queued, one being synthesized.) This bounds memory and the work a cancellation wastes. `synthesize` is called only once the slot is held, so a function that does its work eagerly (kokoro's) is bounded too.
- `sentences` is consumed by the producer, so a lazy iterator (`segment_sentences(pieces)` over the text-stream bridge) blocks the producer, never the worker.
- An exception from `sentences` or `synthesize` is handed to the worker through the queue and raised there unchanged; `synthesize` wraps library errors in `TTSError` itself. Callback exceptions propagate unchanged.
- The producer stops before taking the slot for its next chunk once `stop` is set or the worker has finished (a local `done` event set in a `finally`). The slot wait and the worker's queue `get` wake every 50 ms to recheck, so neither thread can wait on the other forever.
- **The producer is not joined** on cancellation or failure: a running inference call usually cannot be interrupted, and waiting for it would add up to one sentence's inference to every cancellation. It is a daemon thread that finishes that one call, sees the flag and exits. On a normal end it has already exited, since the worker returns only after receiving its end marker.

Users of `feed_with_lookahead`: kokoro and piper. A runtime that streams frames itself (pocket, phonon, qwen3) does not need it. Every backend that groups with `segment_sentences` (pocket, phonon, qwen3, kokoro, piper, mistral, gemini, openai, audiofile) tolerates a short segment; correctness of the audio is unaffected.

### `open_cancellable` — a handshake that honors `stop`

```python
def open_cancellable(
    enter: Callable[[], T],
    leave: Callable[[T], object],
    *,
    stop: threading.Event,
    timeout: float,
    what: str,
) -> T | None:
    """Run a blocking setup step on a helper thread and wait for it while polling ``stop``."""
```

A synchronous SDK's connection setup (`websockets.sync.client.connect` behind Deepgram's `speak.v1.connect` and Cartesia's `websocket_connect`) blocks the worker until the handshake completes, and the SDK does not always let its open timeout be set. The helper runs `enter` on a daemon thread and waits for it in 100 ms polls: it returns the connection, or `None` once `stop` is set meanwhile (the module returns silently: the utterance was cancelled before anything was sent), or raises `TimeoutError` past `timeout` (the module wraps it in `TTSError`); `enter`'s own exception propagates unchanged. A connection that completes **after** the wait gave up is handed to `leave` on the helper thread, so an abandoned handshake is closed rather than leaked; a failure of that late close is logged at DEBUG. The helper thread is one of the threads that may outlive an utterance ("Lifetime and ownership"). Gradium does the same inline on its private loop.

### `slice_text` — a grain for the admission gate

```python
def cut_at_boundary(text: str, room: int) -> int: ...
def slice_text(text: str, max_chars: int) -> Iterator[str]:
    """Yield ``text`` verbatim in slices of at most ``max_chars`` characters."""
```

A native-stream backend forwards a complete text over the same socket as a stream, but `synthesize_text` hands it one piece. Sent whole, that piece would defeat the admission gate below (the server would hold the entire text at once). `slice_text` cuts it, verbatim, into slices of at most `max_chars` characters (deepgram and cartesia pass 50, a few seconds of speech): after the last sentence end inside the window, else after the last whitespace, else hard (a single word longer than the window is the one slice allowed past it). The slices concatenate back to the text exactly; the backend's own word grouping and segmentation apply to them as to an LLM's pieces. `cut_at_boundary` is the same rule for one cut, which Deepgram's segmenter also uses.

### `cleanup` — a failing release never hides the outcome

```python
@contextlib.contextmanager
def cleanup(close: Callable[[], object], what: str) -> Iterator[None]:
    """Run ``close()`` when the block ends, without letting it hide the block's own outcome."""
```

Every module releases something at the end of an utterance — an HTTP response, a socket, a decoder, the sink's `drain` in the engine — and a release can fail (a socket already broken, a device gone). A bare `try/finally` would let that failure **replace** the utterance's own exception, so a caller promised the callback's `ValueError`, the source's error or `CancelledError` would see an `OSError` from the close instead. The helper fixes the precedence: if the block raised, `close()` runs and its failure is **logged at WARNING** with the original exception propagating unchanged; if the block completed, `close()` runs and its failure propagates, since it is then the only thing that went wrong. The engine wraps `drain` in it, and every module wraps its release step (`with cleanup(response.close, "OpenAI response"):`); Deepgram and Cartesia, which close off the worker thread, log a close failure the same way. A module with an async release (gradium) applies the same rule inline.

## Bounded unread audio

The text bridge bounds how far a **source** runs ahead of the worker. The native-stream providers (deepgram, cartesia) also bound how far the **provider** runs ahead of playback, since both servers generate several times faster than real time and would otherwise pile up unread PCM for the length of the utterance (about 48 kB per second of audio ahead at 24 kHz). The rule, the same in both modules:

- The reader thread counts the audio it has read into the worker's inbox and not yet fed (`unread`); the worker subtracts as it feeds.
- The sender **admits** a text message only while `unread` is under **10 s** of audio at the module's rate, polling `stop` meanwhile. Text admission is thereby coupled to playback progress: with the default sink the server is fed at the pace it is played, after an initial lead of ten seconds.
- What the server already holds is bounded by the provider's own acknowledgement, not by `unread`: Deepgram's flushed segments (at most two of ≤ 900 characters unanswered, "Pending-text backpressure" in its spec) and Cartesia's one segment per context, the next opened only after the server's `done`. So the audio read and not yet fed never exceeds the lead plus the audio of those segments: in the order of a minute, not of the utterance.
- A complete text is sent in `slice_text` slices so the gate has a grain; a stream's word groups are a grain already.

This is a bound, not a latency device: the lead is what protects playback from a network stall. The values are module constants (no config field until there is a reason), and each module's spec states what it measured against the live service.

## Shared config helpers

Every module validates its own `engine.module` block in its constructor ("Error handling" below), but most of the fields are of three kinds every backend shares, and the first review of the modules found the same check copied ten times with small drifts (one copy accepting `NaN`, another a bool). `modules/base.py` therefore holds one validator per kind, and a module never re-implements them. Each takes a `label` (`"ElevenLabs module"`) that prefixes its messages, so an error still names the backend:

```python
def resolve_api_key(config: dict, *, label: str) -> str: ...
def optional_str(config: dict, field: str, *, label: str, default: str | None = None) -> str | None: ...
def finite_number(
    config: dict, field: str, *, label: str, default: float | None = None,
    minimum: float | None = None, maximum: float | None = None, exclusive_minimum: float | None = None,
) -> float | None: ...
```

- **`resolve_api_key`** is the credential rule every API-backed module follows ([elevenlabs-module.md](modules/elevenlabs.md), "API key resolution", is its reference description): a non-empty literal `api_key` wins; otherwise the environment variable named by `api_key_env` is read, and its being unset or empty is a `ConfigError` naming the variable; a non-string value for either field, or neither yielding a key, is a `ConfigError`. The key is always passed to the SDK explicitly, so no SDK's own environment fallback is in force.
- **`optional_str`** reads a field that may be absent: absent gives `default` (`None` without one); present must be a non-empty string (`"<label> '<field>' must be a non-empty string"`). The **absent-versus-`null` policy** the modules chose is kept: with no `default`, an explicit `null` counts as absent; with a `default`, an explicit `null` is rejected like any other non-string, since a value the config names is validated and only a missing one is defaulted. A module that wants `null` to mean "the default" with a default of its own writes `optional_str(...) or DEFAULT` (qwen3, kokoro); a module whose field must not be `null` even without a default keeps its own `"field" in config` check (pocket's `voice`, audiofile's `default_file`).
- **`finite_number`** reads a numeric field with the same absent/`null`/`default` policy, as a `float`: a present value must be an `int` or `float` (a bool is rejected rather than treated as an integer), **finite** (`NaN` and the infinities, which JSON's parser accepts, would otherwise reach a sampler or a timeout) and within the bounds given (`minimum`/`maximum` inclusive, `exclusive_minimum` exclusive); the message names the field and the bounds (`"... must be a finite number > 0"`, `"... between 0 and 1"`). The `timeout` field of every API-backed module is `finite_number(config, "timeout", label=..., default=30.0, exclusive_minimum=0)`.

Integer fields (`sample_rate`, `max_tokens`, `max_buffer_delay_ms`) and enumerations (`device`, `language`) stay with the module: each has its own rule and there is one of each.

## Shared PCM helpers

Three more helpers in `modules/base.py` cover the audio side of the contract once, for the same reason:

```python
def float_to_int16_pcm(samples) -> bytes: ...
def slice_bytes_for(sample_rate: int) -> int: ...

class PCMFeeder:
    def __init__(self, callback, stop, *, slice_bytes: int, sample_bytes: int = 2, convert=None) -> None: ...
    def feed(self, data: bytes) -> bool: ...
```

- **`float_to_int16_pcm`** is the one float → int16 conversion ("Audio format contract"): any array-like of float samples, flattened to mono, clipped to ±1.0, scaled by 32767, as little-endian `int16` bytes; the input is not modified. The local models that yield float waveforms (pocket, phonon, qwen3, kokoro) and mistral's float32 byte stream all go through it; pocket keeps its own tensor-to-CPU step in front of it, since that is torch-specific.
- **`slice_bytes_for(sample_rate)`** is 100 ms of int16 mono at that rate (`SLICE_SECONDS`): the most a slicing module hands `callback` in one call. `AudioPlayer.feed` blocks for the whole chunk it is given, and `stop` is polled between slices, so a cancel is honored within one slice's playback whatever the backend's own chunking. `feed_with_lookahead` users (kokoro, piper) pass it as `slice_bytes`.
- **`PCMFeeder`** is the one alignment-and-slicing loop for the providers that deliver a **byte stream** whose chunk boundaries are arbitrary (openai, mistral, gemini, deepgram, cartesia). One per utterance, on the worker thread: `feed(data)` prepends what the previous call left over (a sample split across two chunks), keeps the trailing partial sample (`sample_bytes`, 2 for int16, 4 for mistral's float32) for the next call, converts the whole samples through `convert` when given (mistral: `float_to_int16_pcm` over the float32 bytes), and hands the result to `callback` in slices of at most `slice_bytes`, checking `stop` before each. It returns `False` as soon as `stop` is seen, so the module's loop returns without reading further; a partial sample left at the end of the stream is never fed. Before it, the carry and the slice loop were five private copies, and gemini fed its parts unsliced on the strength of the part sizes it had observed; the feeder removes that assumption.

The fixture modules and the providers whose SDK already yields whole int16 samples at a known size (elevenlabs through its decoder, gradium's 80 ms frames, piper's per-sentence `int16` bytes through `feed_with_lookahead`) do not need the feeder.

## Local-model modules

The API-backed pattern (ElevenLabs) is one shape; a second shape is a **local-model module** that runs inference in-process (e.g. Kokoro, ChatTTS, Piper). The [`huggingface/speech-to-speech`](https://github.com/huggingface/speech-to-speech/tree/main/src/speech_to_speech/TTS) TTS handlers are a useful catalogue of which engines work well and how to invoke them, but they are **not** reusable as-is: each extends that project's `BaseHandler[TTSIn, TTSOut]` pipeline (a `setup()`/queue/`CancelScope` framework), yields `numpy` arrays at 16 kHz, and pulls in the whole app package. A local-model module here wraps the **underlying model library directly** (e.g. the `kokoro` package), not the handler, and honors this interface:

- **Config-driven construction.** `__init__` validates the `engine.module` block and loads the model (or defers the load to first use). Model download / device selection are config fields, mirroring `api_key_env`/`voice_id` on ElevenLabs.
- **`sample_rate` reports the module's native rate** (commonly 24000 for these models), so no resampling is needed unless the backend itself varies.
- **Inference runs off the event loop.** Local inference is blocking and CPU/GPU-bound; run it inside `asyncio.to_thread` (as ElevenLabs does for its blocking SDK iterator), driving `callback` from that single worker thread so calls never overlap.
- **Float → int16 conversion** happens in the module before the callback, through the shared `float_to_int16_pcm` (clip to ±1.0, scale to the int16 range; "Shared PCM helpers" above), since these libraries yield float waveforms.
- **Cancellation and errors** follow the same rules as any module: stop calling `callback` before returning/raising, and wrap backend/inference failures in `TTSError`. When the library accepts a stop signal of its own (pocket-tts's `stop` event, Phonon's stream `close()`, closing the qwentts.cpp generator), the worker hands it the cancellation so inference stops too, not just the callbacks. A library with no stop signal and whole-sentence output (kokoro-onnx, piper-tts) stops between sentences and between 100 ms feed slices instead, through `feed_with_lookahead`.
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
- **Cleanup never replaces the outcome.** A failing release (close, drain, context exit) after a synthesis error, a callback error, a source error or a cancellation is logged, not raised; with no primary error it is raised. See `cleanup` above.

## Open questions

Deferrals only (design is settled):

1. **Configurable `max_chars`** for `segment_sentences`. A fixed default serves the known backends; a per-module config field (or a `TTSOptions` field) can be added without changing the helper's shape if a backend wants shorter or longer segments.
2. **Resolved: bounded unread audio** (see "Bounded unread audio" above). Kept for the record of what was deferred: The text bridge is bounded (above), but audio a provider has already sent and the worker has not fed yet is not: Deepgram's reader thread queues every message in an unbounded inbox, and Cartesia's socket runs with receive flow control off (`max_queue: None`) because the default limit made an aborted utterance wait for the 10 s close timeout. With the default sink the worker feeds in real time while both servers generate several times faster, so unread PCM grows with the utterance's length — at 24 kHz about 48 kB per second of audio ahead, a few MB for a minute-long reply. Bounding it means coupling text admission to playback progress (a bounded inbox would stall the reader, hence its `Flushed` acknowledgements, hence the sender's gate; Cartesia would need a close path that works with a full receive queue) and has to be validated against the live services. Deferred; the per-module specs state the current behaviour.
