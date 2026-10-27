# Lifecycle and cleanup fixes

**Status:** Done

Implements the settled behaviour in [tts-module-interface.md](../specs/tts-module-interface.md) ("What cancellation guarantees", "`cleanup`"), [audio-sink.md](../specs/audio-sink.md) ("Call discipline"), [pocket.md](../specs/modules/pocket.md) ("The generator is drained"), [gradium.md](../specs/modules/gradium.md) ("Setup is cancellable and bounded"), [elevenlabs.md](../specs/modules/elevenlabs.md) ("Text stream", `timeout`) and the `timeout` field of the openai, mistral and gemini specs. Closes findings F2 to F6 of the 2026-10-26 module code review: a cancel could not interrupt a stalled request or a connection setup (F2); pocket abandoned the library's generator so its decoder thread was never joined before the model was reused (F3); gradium leaked a half-opened connection when the setup failed (F4); elevenlabs' stream-input loop ignored protocol error messages and took a clean close before `isFinal` for success (F5); a failing drain or close replaced the utterance's own exception (F6). Leaves out: interrupting a blocked network read from outside (the timeouts bound the wait instead), and the deepgram/cartesia connection setup, which stays under the SDK open timeout.

## Scope

- `src/tts_engine/modules/base.py` — the `cleanup(close, what)` context manager (block's exception wins; close failure logged, or raised when alone).
- `src/tts_engine/engine.py` — `say` / `say_stream` drain through `cleanup`.
- `src/tts_engine/modules/pocket/module.py` — `_settle` drains the generator in a `finally`, logging a library error while draining.
- `src/tts_engine/modules/gradium/module.py` — `timeout` field; `_open(stop)` runs `__aenter__` as a task polled against `stop` and the deadline, `_leave(tts_cm, failed=…)` closes (logging after a failure) on every exit path, including a failed or cancelled entry; both consumers return early when setup was cancelled.
- `src/tts_engine/modules/elevenlabs/module.py` — `timeout` field (`request_options.timeout_in_seconds` on the HTTPS request, `open_timeout` on the socket); `error` messages and a clean close before `isFinal` are `TTSError`; already-labeled errors are not re-wrapped; the raw source is released through `cleanup`.
- `src/tts_engine/modules/{openai,mistral,gemini}/module.py` — `timeout` field forwarded as the SDK's `timeout` / `timeout_ms` / `http_options.timeout`; the response, event stream or generator released through `cleanup`.
- `src/tts_engine/modules/{phonon,qwen3}/module.py` — the stream / generator released through `cleanup`.
- `tests/modules/test_tts_module_interface.py`, `tests/test_engine.py`, `tests/modules/test_{pocket,gradium,elevenlabs,openai,mistral,gemini,phonon,qwen3}.py` — one test per behaviour (below).
- The specs named above, plus the cartesia and deepgram open questions on setup cancellation.

## Steps

1. **`cleanup`** (`base.py`): `try: yield / except BaseException: close() guarded, log, re-raise / else: close()`. Tests: block error over close error (logged), cancellation over close error, close error alone raised, close once on success.
2. **Engine drain** through `cleanup`. Tests: synthesis error, cancellation and a lone drain failure.
3. **Pocket drain** (`_settle`). Fake: the generator records completion and yields one more frame after `stop`. Tests: after a cancel and after a callback failure the generator completed and the extra frame was not fed.
4. **Gradium setup** as in Scope. Fake `Tts` gains `opened`, `fail_enter`, `hang_enter`, `fail_exit`. Tests: failed entry closes and raises `TTSError`; never-ready setup raises after `timeout` (0.1 s in the test) with the connection closed; a cancel during setup propagates promptly with the connection closed; a callback error survives a failing close.
5. **ElevenLabs protocol** as in Scope. Fake socket answers the first text with a scripted error payload or a `ConnectionClosedOK`, and can fail on exit. Tests: the error message is `TTSError` with nothing fed; the premature clean close is `TTSError`; a callback error survives a failing close; `timeout` reaches the HTTPS request (rounded up to whole seconds) and the socket's `open_timeout`.
6. **HTTP providers' `timeout`** (openai, mistral, gemini): validation rows, the default and a set value reaching the SDK call or client, and a callback error surviving a failing close (fakes gain `raise_on_close`). Phonon and qwen3: the close-precedence test only.
7. **Specs**: the cancellation-milestones section, the `cleanup` section, the drain affinity bullets, each module's `timeout` row and validation line, and the testing inventories.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` — green. Live: `tests-e2e/test_modules.py` for elevenlabs, gradium, openai, mistral, gemini, tone and audiofile (35 cases) passed against the real services with the keys present, which exercises the stream-input close rule and the timeouts for real.
