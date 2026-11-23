# Utterance timing

**Status:** Done

Implements [specs/utterance-timing.md](../specs/utterance-timing.md): one OpenTelemetry span per `say` / `say_stream` call with an event per step, and the same stamps returned as an `UtteranceResult`. Leaves out module-level events, any exporter, and the benchmark, which keeps its own chunk timelines.

## Scope

- `pyproject.toml`, `uv.lock` — `opentelemetry-api>=1.20,<2` in the base dependencies; `opentelemetry-sdk` in the `dev` group
- `src/tts_engine/utterance.py` — new: the names (tracer, span, events, attributes), `UtteranceRecorder`, `note_segment`, `UtteranceResult`, `UtteranceTiming`
- `src/tts_engine/engine.py` — `say` / `say_stream` open a recorder, hand the module a counted text iterator and a stamped `feed`, and return the result; the engine remembers the default `AudioPlayer` it built
- `src/tts_engine/audio.py` — `AudioPlayer.output_latency`
- `src/tts_engine/modules/base.py` — `segment_sentences` reports each segment it yields; `feed_with_lookahead` starts its producer in a copy of the worker's context
- `src/tts_engine/__init__.py` — exports `UtteranceResult`, `UtteranceTiming`
- `src/tts_engine/audition/audition.py` — time to first audio read from the result instead of its own clock
- `tests/test_utterance.py` — new; `tests/test_audio.py` — `output_latency`; `tests/test_docs_examples.py` — the new page's example
- `tests/test_engine.py`, `tests/test_docs_examples.py`, `tests/test_no_audio_import.py` — stand-ins for the player and the module gain the member the engine now reads (`output_latency`, `kind`)
- `specs/utterance-timing.md` — new; `specs/architecture.md`, `audio-sink.md`, `audio-player.md`, `tts-module-interface.md`, `project.md`, `voice-audition.md`, `_index.md` — the cross-references
- `docs/utterance-timing.md` — new; `docs/index.md`, `docs/audio-sinks.md`, `README.md`, `AGENTS.md` — links and the project map

## Steps

1. **Dependencies.** Add `opentelemetry-api` to `[project].dependencies` with its bound and reason, `opentelemetry-sdk` to the `dev` group; `uv sync --dev`.
2. **`utterance.py`.** The name constants. `UtteranceRecorder`: reads the wall clock once and the monotonic clock for offsets; starts the span in the current context with the four start attributes; as a context manager makes the span current and sets the context variable `note_segment` reads, and on exit ends the span with the outcome (`completed`, `cancelled` for `CancelledError`, `failed` with the exception recorded and an error status). `_mark` adds an event once, under a lock, with an explicit timestamp. `text_stream(source)` returns an iterator that stamps `first_text` and counts characters without owning the source. `feeder(feed, output_latency)` stamps `first_audio` / `last_audio` on entry and, when given the default player's latency reader, `playback_started` after the first chunk's `feed` returns. `result()` builds the dataclasses from the stamps.
3. **Engine.** Build the default player in the constructor so the engine knows whether it is an `AudioPlayer`; keep the resolved module type. In `say` and `say_stream`, wrap the whole call in the recorder, stamp `admitted` after `_check_open`, hand the wrapped iterator and callback to the module, return `utterance.result()`. `say_stream` raises its `TypeError` for a `str` before opening a span.
4. **Player.** `AudioPlayer.output_latency`: the open stream's `latency` when it is a number, else `None`.
5. **Helpers.** `segment_sentences` calls `note_segment()` before each `yield`; the recorder ignores it for a complete text. `feed_with_lookahead` starts its producer with `contextvars.copy_context().run`.
6. **Audition.** Compute each run's `ttfa_ms` from `first_audio_at − started_at`; drop the sink's own stamp.
7. **Tests.** `tests/test_utterance.py` on the `tone` and `audiofile` modules with the SDK's `InMemorySpanExporter` (installed per test by patching `opentelemetry.trace.get_tracer_provider`, so the rest of the tier runs with no SDK configured) and a scripted clock in place of `time` in `utterance.py`:
   - one span per call, a child of the caller's, with its scope name and attributes; the span current in the worker thread and in the text source;
   - the events present and ordered for a text stream; none of the text events for a complete text; no `first_segment` on a module that does not segment; `first_segment` from the lookahead thread;
   - `first_audio` / `last_audio` at the time of their `feed`, from a worker thread; a late first piece moves `first_text` by as much; a queued call admitted after the one in flight ends;
   - outcomes: cancelled, cancelled while queued (no `admitted`), failed (exception recorded), closed engine;
   - the default player's `playback_started` and latency, a device reporting none, no such event for an injected sink or the discard player;
   - the result mirrors the span; the result with no SDK; an utterance with no audio; the exports.
8. **Specs, docs, map.** Write the spec and the user page; update the cross-references, both indexes and the AGENTS.md project map.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` all pass. The live tier's fixture rows (`tone`, `audiofile`) pass against the real audio output, confirming a real device stream reports a latency.
