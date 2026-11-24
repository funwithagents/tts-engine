---
code:
  - src/tts_engine/utterance.py
  - src/tts_engine/engine.py
  - src/tts_engine/audio.py
  - src/tts_engine/modules/base.py
  - src/tts_engine/__init__.py
tests:
  - tests/test_utterance.py
  - tests/test_audio.py
---

# Utterance timing

**Status:** Implemented

## Purpose

An application that speaks through the engine wants to know how long its listener waited and where that time went: behind another utterance, waiting for the text, in synthesis, opening the sound device. Before this spec `say` and `say_stream` returned `None` and reported nothing; time to first audio was only measured by tools that wrapped a sink around the engine themselves.

The engine now reports the steps of every utterance, in two forms filled from the same stamps:

- **An OpenTelemetry span** per `say` / `say_stream` call, with one event per step. An application that traces its own work gets the utterance placed inside its trace with no code of its own: the span is a child of whatever span is current where the call is awaited.
- **An `UtteranceResult`** returned by the call, for a caller with no tracing setup.

The engine measures; it does not display, export or aggregate. Nothing here changes how an utterance is synthesized or played, and there is no configuration.

## Core concepts / Decided

### The names are a contract

A trace reader finds an utterance by its tracer and span name, and reads its steps from the event names. These names are public and stable: renaming one is a breaking change. They are constants in [utterance.py](../src/tts_engine/utterance.py).

| Kind | Name | Meaning |
|---|---|---|
| Tracer | `tts_engine` | the instrumentation scope of every span the package opens |
| Span | `tts_engine.utterance` | one `say` or `say_stream` call, from the call to its end |
| Event | `admitted` | the say lock is held and the engine is open: the utterance starts. Before it, the call waited behind another utterance |
| Event | `first_text` | the first non-empty piece is taken from the text stream |
| Event | `first_segment` | the first sentence is handed to synthesis by `segment_sentences` |
| Event | `first_audio` | the first non-empty chunk reaches the sink |
| Event | `last_audio` | the last non-empty chunk reaches the sink |
| Event | `playback_started` | the default player has written its first chunk to the device |
| Attribute | `tts_engine.input` | `text` (`say`) or `text_stream` (`say_stream`) |
| Attribute | `tts_engine.module` | the module's registered type (a renamed type under its current name) |
| Attribute | `tts_engine.module_kind` | `hosted`, `local` or `fixture` ([tts-module-interface.md](tts-module-interface.md), "Module kinds") |
| Attribute | `tts_engine.sample_rate` | Hz |
| Attribute | `tts_engine.text_chars` | characters of text consumed |
| Attribute | `tts_engine.audio_seconds` | seconds of audio fed to the sink |
| Attribute | `tts_engine.outcome` | `completed`, `failed` or `cancelled` |
| Attribute | `tts_engine.output_latency_seconds` | on the `playback_started` event, when the device reports it |

Attributes carry the package's name as their prefix, like the tracer: a trace holds spans from many libraries, and `tts.` could be another library's or a future OpenTelemetry convention's. Event names need no prefix, since an event only exists inside its span. The first four attributes are set when the span starts; `text_chars`, `audio_seconds` and `outcome` when it ends.

### Events, not child spans

The steps are instants, not nested intervals: in `say_stream` text keeps arriving while audio is already playing, so "waiting for text", "synthesizing" and "playing" overlap. One span with timestamped events lets each reader define the durations it cares about (`first_audio − first_text`, `admitted − start`). Every event appears **at most once** per span, and only when its step happened.

### What each event means, and when it is absent

- **`admitted`** is stamped once the call holds the say lock and has found the engine open. A call cancelled while it waits for the utterance in flight, or one that finds the engine closed, has no `admitted` event.
- **`first_text`** and **`first_segment`** describe the wait for text, so they exist for `say_stream` only. A complete text is all there at the call: `say` emits neither, including on the modules that cut a complete text into sentences (kokoro, piper, qwen3).
- **`first_segment`** is stamped by the shared `segment_sentences` helper when it yields its first segment, so it is present for the modules that group a text stream into sentences (pocket, phonon, qwen3, kokoro, piper, mistral, gemini, openai, openai-speech-compatible, audiofile) and absent for those that forward text as it arrives (elevenlabs, gradium, deepgram, cartesia) or per piece (tone). Its distance from `first_text` is the time the text source took to finish its first sentence: without it, a slow writer would read as a slow voice.
- **`first_audio`** and **`last_audio`** are stamped when `feed` is **entered** for the first and the last non-empty chunk, before the sink does anything with it. An utterance that produced no audio has neither. With one chunk they carry the same time.
- **`playback_started`** is stamped when the first non-empty chunk's `feed` **returns** on the engine's default device player (`AudioPlayer`), and carries the stream's reported output latency. The player opens the device inside that first `feed`, so `playback_started − first_audio` is the device's opening time, and `playback_started + output_latency_seconds` estimates when sound leaves the speaker. It is absent for an injected sink, whose playback is its owner's to report, and for the `DiscardPlayer`, which plays nothing.

### The span

- **Opened when the call starts, in the caller's context**, before the lock is requested. It is therefore a child of the span current where `say` / `say_stream` is awaited, and its duration includes the wait behind another utterance.
- **Current while the engine runs.** The span is made current for the length of the call, so a span opened below it nests under it: in a module's worker (`asyncio.to_thread` copies the context), and in the text source's own code, which runs when the engine pulls a piece. A module may later add finer spans of its own (a provider's round trip); none does today.
- **Ended on every exit path**, after the sink has been drained and the text source closed, with its outcome:
  - `completed`: the call returns.
  - `cancelled`: the call exits with `asyncio.CancelledError`. The span's status stays unset: a cancel is a decision of the caller, not an error.
  - `failed`: any other exception. The exception is recorded on the span and its status set to error.
- A `say_stream` given a bare `str` raises `TypeError` before any span is opened: no utterance was asked for.

### The clock

Stamps are **wall-clock**, so they compare with the stamps of other packages in the same trace: nanoseconds since the epoch on the span, UTC `datetime`s in the result. Each utterance reads the wall clock **once**, when the call starts, and adds monotonic offsets (`time.perf_counter_ns`) from there. So durations inside an utterance have the monotonic clock's resolution and never run backwards when the system clock is adjusted, while a long-lived process does not drift away from the wall clock (a process-wide anchor would, across a host sleep).

### Stamps from any thread

`feed` runs on the module's worker thread, and the sentences of kokoro and piper are pulled in a plain `threading.Thread` (`feed_with_lookahead`'s producer). The recorder never relies on "the current span" to place an event: it holds the span object and calls `add_event` on it with an explicit timestamp, under a lock that makes each event first-call-wins.

The one stamp taken below the engine, `first_segment`, reaches the recorder through a context variable set for the length of the call. `asyncio.to_thread` and `create_task` copy the context; a plain thread starts with an empty one, so `feed_with_lookahead` starts its producer in a copy of the worker's context ([tts-module-interface.md](tts-module-interface.md), "`feed_with_lookahead`"). A helper that starts a thread which pulls text must do the same.

### Where the stamps are taken

Everything but `first_segment` is stamped by the engine, with no change to the module interface:

- the text iterator handed to the module is wrapped to count characters and stamp `first_text`;
- the callback handed to the module is the sink's `feed` wrapped to stamp `first_audio` / `last_audio`, count bytes and, for the default player, stamp `playback_started`. It wraps the gain step too, so the stamps do not depend on `gain_db`.

`AudioPlayer.output_latency` ([audio-player.md](audio-player.md)) is what the engine reads for the latency. The `AudioSink` Protocol is unchanged: a custom sink implements nothing new.

### `UtteranceResult`

```python
@dataclass(frozen=True)
class UtteranceTiming:
    started_at: datetime                 # the call starts
    admitted_at: datetime
    first_text_at: datetime | None
    first_segment_at: datetime | None
    first_audio_at: datetime | None
    last_audio_at: datetime | None
    playback_started_at: datetime | None
    output_latency_seconds: float | None
    ended_at: datetime                   # the sink is drained, the source closed


@dataclass(frozen=True)
class UtteranceResult:
    text_chars: int
    audio_seconds: float
    timing: UtteranceTiming
```

- `say` and `say_stream` return it. They returned `None` before, so a caller that ignores the value is unaffected.
- The fields mirror the span: each `<event>_at` is that event's time, `started_at` / `ended_at` the span's bounds, `None` wherever the event is absent. Datetimes are timezone-aware UTC, at microsecond precision.
- It is returned **only when the call completes**. A failed or cancelled call raises as before; its stamps are on the span.
- It carries no outcome (always `completed`), and neither the module nor the sample rate, which the engine already exposes.
- The stamps are taken whether or not an OpenTelemetry SDK is installed.
- Both types are exported from `tts_engine`. `TTSTools.say` and the MCP `say` tool keep their string contract; the span exists for them too.

Time to first audio is `first_audio_at − started_at`. The voice audition reads it from here ([voice-audition.md](voice-audition.md)), so the project has one definition of it.

### Dependency

`opentelemetry-api` is a base dependency ([project.md](project.md), "Key dependencies"): the API only, never the SDK. With no SDK installed, the span is a no-op object and its events cost nothing beyond the clock reads the result needs anyway. An application that wants the spans installs the SDK and an exporter of its choice; the engine has no exporter, no enable flag and no configuration. The SDK is a `dev` dependency, for its in-memory exporter in the tests.

The tracer is obtained per utterance (`trace.get_tracer("tts_engine")`), so an SDK installed after the engine was built is picked up.

## Open questions

Deferrals only:

- **Module-level events or spans** (request sent, first byte from the provider) would split `first_audio − first_segment` further. The span is already current in the worker, so a module can add them without an engine change. Not needed yet.
- **The benchmark** keeps its own chunk timelines ([benchmark.md](benchmark.md)): it needs every chunk's time, not the first and last.
- **A `tracer_provider` argument on `TTSEngine`**, the usual way to give a library a provider other than the global one. Nobody needs it yet.
- **More in `UtteranceResult`**, such as the gain step's clipped-sample count, which is only logged today.
