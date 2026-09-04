# Pluggable audio sink

**Status:** Done

Implements the settled behavior in `specs/audio-sink.md` (whole spec) and the sink-related edits in `specs/architecture.md` ("`TTSEngine` construction", "Public API") and `specs/audio-player.md` ("Lazy sounddevice import", `AudioSink` conformance). Delivers an `AudioSink` Protocol, constructor sink injection on `TTSEngine`, a `TTSEngine.sample_rate` property, and a lazy sounddevice import so the engine is importable without local audio. Deliberately leaves out a per-call `speak(..., sink=...)` override (deferred in the spec) and any resampling/format conversion (the sink's concern).

## Scope

The exact files this plan touches, each with a one-line note on what changes:

- `src/tts_engine/audio.py` — add `AudioSink` Protocol; declare `AudioPlayer(AudioSink)`; move `import sounddevice` from module top into `feed()` (lazy).
- `src/tts_engine/engine.py` — `__init__(config, *, sink=None)`: use injected sink or build default `AudioPlayer`; `speak` drives `self._sink`; add `sample_rate` property.
- `src/tts_engine/__init__.py` — export `AudioSink` in `__all__`.
- `tests/test_audio.py` — keep sounddevice-patch tests green under the lazy import; assert `AudioPlayer` satisfies `AudioSink`.
- `tests/test_engine.py` — inject a fake in-memory sink; assert it receives the chunks and exactly one `drain` (including on failed/cancelled `stream`); assert the default path still builds `AudioPlayer`; assert `sample_rate`; assert no `AudioPlayer` is built when a sink is injected.
- `tests/test_no_audio_import.py` (new) — with sounddevice made unimportable, `import tts_engine` + `TTSEngine(config, sink=fake)` + `speak` succeed and never import sounddevice.
- `tests-e2e/test_engine.py` — add a sibling live scenario driving the real ElevenLabs pipeline into an in-memory capture sink, asserting real int16-mono PCM reached the public seam.

## Steps

Ordered, buildable steps.

1. **`AudioSink` Protocol** in `audio.py`. Add `from typing import Protocol` and define `AudioSink` with `feed(self, chunk: bytes) -> None: ...` and `drain(self) -> None: ...`, plus the PCM-contract docstring from the spec. Place it above `AudioPlayer`.

2. **Declare conformance.** Change `class AudioPlayer:` to `class AudioPlayer(AudioSink):`. No other change to the class shape.

3. **Lazy sounddevice import.** Remove the top-level `import sounddevice as sd` from `audio.py`. Inside `feed()`, before opening the stream, do a local `import sounddevice as sd`. Keep `numpy` top-level. The type annotation on `self._stream` currently references `sd.OutputStream` at module scope — replace it with a string/`Any`-safe form so no top-level sounddevice symbol is needed (e.g. annotate `self._stream: "sd.OutputStream | None"` won't resolve without the import; use `self._stream = None` with a module-level `if TYPE_CHECKING: import sounddevice as sd` guard so pyright still types it while runtime import stays lazy).

4. **Engine constructor.** `def __init__(self, config: TTSEngineConfig, *, sink: AudioSink | None = None)`. Set `self._sink: AudioSink = sink if sink is not None else AudioPlayer(device=config.player.device, sample_rate=self._module.sample_rate)`. Import `AudioSink` (and keep `AudioPlayer`) from `tts_engine.audio`. When `sink` is provided, `AudioPlayer` is never called.

5. **`speak` drives the sink.** Replace `self._player` references with `self._sink`: `callback=self._sink.feed` and `self._sink.drain()` in the `finally`. Keep the `try/finally` intact.

6. **`sample_rate` property.** Add a read-only `sample_rate` property returning `self._module.sample_rate`.

7. **Export.** Add `AudioSink` to `__init__.py`: `from tts_engine.audio import AudioSink` and include it in `__all__`.

8. **Tests — audio.** Confirm existing `tests/test_audio.py` still passes: the sounddevice-patch tests patch `sounddevice.OutputStream`, which the lazy `import sounddevice as sd` inside `feed()` resolves at call time, so patches still apply. Add a test asserting `AudioPlayer` is an `AudioSink` (structural — e.g. via a `TYPE_CHECKING` assignment or `isinstance` only if the Protocol is `runtime_checkable`; prefer a static assignment `_: AudioSink = AudioPlayer(sample_rate=44100)` in the test to keep it import-only).

9. **Tests — engine.** Add cases:
   - Inject a fake sink recording `feed` chunks and `drain` count; assert chunks arrive and `drain` is called exactly once on success, on `TTSError`, and on `asyncio.CancelledError`.
   - Injected-sink construction does **not** call `AudioPlayer` (patch `tts_engine.engine.AudioPlayer`, assert not called).
   - Default (no-sink) construction still builds `AudioPlayer(device=..., sample_rate=module.sample_rate)` — existing test stays.
   - `engine.sample_rate == module.sample_rate`.

10. **Tests — no-audio import.** New `tests/test_no_audio_import.py`: run a subprocess (clean interpreter) that sets `sys.modules["sounddevice"] = None` before `import tts_engine`, then constructs `TTSEngine(config, sink=fake)` and runs `speak`, asserting success and that `"sounddevice"` was never really imported. Subprocess keeps the poisoned `sys.modules` out of the rest of the suite.

11. **E2E — capture sink.** Add a sibling test in `tests-e2e/test_engine.py` (same "library path" scenario, no MCP, no audio hardware needed): define a small in-memory capture sink (`feed` appends chunks to a `bytearray`, `drain` sets a flag), inject it via `TTSEngine(app_config.engine, sink=capture)`, `await engine.speak(...)`, then assert the tier's robust properties — some bytes were produced (`len(capture.data) > 0`), the buffer is whole int16 samples (`len(capture.data) % 2 == 0`), `drain` ran exactly once, and `engine.sample_rate == app_config...`'s module rate. Reuses the existing `app_config` fixture, so it skips cleanly without `ELEVENLABS_API_KEY`. No audio-content assertion, per testing.md.

12. **Statuses.** On green: flip `specs/audio-sink.md` to `Implemented`, `specs/architecture.md` and `specs/audio-player.md` back to `Implemented`, mirror all three in `specs/_index.md`, and set this plan + its `plans/_index.md` row to `Done`.

## Verification

- `uv run ruff check .`
- `uv run ruff format .`
- `uv run pyright`
- `uv run pytest` — full default tier green, including the new engine sink tests, the audio conformance test, and `tests/test_no_audio_import.py`.
- Spot-check the drift guard: `uv run pytest tests/test_project_map.py` (spec frontmatter / map invariants hold with the new `audio-sink.md` and updated map rows).
- E2E (opt-in, requires a key): `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e/test_engine.py'` — the new capture-sink scenario passes against the real API; it skips cleanly when `ELEVENLABS_API_KEY` is unset.

Mark this plan `Done` (here and in [_index.md](_index.md)) and promote the three specs only once all pass.
