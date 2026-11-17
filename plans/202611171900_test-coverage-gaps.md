# Unit tier coverage gaps

**Status:** Done

Adds the tests the unit-tier review of 2026-11-17 found missing: spec-listed paths with no test. One of them exposed a gap in Pocket's code, fixed here. Builds on [Unit tier cleanup](202611171800_test-tier-cleanup.md).

## Scope

- `tests/test_tools.py`, `tests/test_mcp.py` — the error paths of the tools layer and the MCP server
- `tests/modules/test_tts_module_interface.py` — `split_at_last_space`; `run_cancellable_worker` discarding a worker error after a cancel
- `tests/modules/test_deepgram.py`, `test_cartesia.py` — voice-listing failure, close after a dead socket, the 403 and other-status handshake rejections
- `tests/modules/test_mistral.py`, `test_gemini.py`, `test_openai_speech_compatible.py` — text-stream cancel with the source open
- `tests/modules/test_piper.py`, `test_kokoro.py` — `list_voices` failures
- `tests/modules/test_pocket.py`, `src/tts_engine/modules/pocket/module.py` — the frame decoded after `stop` is never fed
- `tests/modules/test_tone.py` — text-stream callback error, cancel while the source is idle
- Editorial: `specs/testing.md`, `specs/tts-module-interface.md`, the *Testing* sections of the matching module specs

## Steps

1. **Tools and MCP.** A non-`TTSError` propagates from `TTSTools.say` unchanged (`specs/tools.md`); through `FastMCP.call_tool`, a `TTSError` comes back as text content and any other error as the SDK's `ToolError` with the original as its cause (`specs/mcp-server.md`).
2. **Shared helpers.** `split_at_last_space` gets its own test and joins the helper list of `specs/testing.md`; `specs/tts-module-interface.md` now names all four modules that use it. `run_cancellable_worker`: a worker that raises while unwinding a cancel leaves `CancelledError` as the outcome.
3. **Deepgram and Cartesia.** `list_voices` failure → `TTSError`; `close()` after the held socket died completes and exits its manager once; Cartesia 401 and 403 → `ConfigError`, another status → `TTSError`.
4. **Mistral, Gemini, openai-speech-compatible.** Text-stream cancel with the source still open: `CancelledError`, the request's stream or response closed, nothing fed after the cancel.
5. **Piper and Kokoro.** `list_voices` download and unreadable-file failures → `TTSError`.
6. **Pocket.** The loop checked `stop` only before `next()`: a cancel landing while `next()` was blocked in inference let the frame it returned be fed, against `specs/modules/pocket.md` ("never fed"). The loop now re-checks `stop` after `next()` returns. A test whose fake sets `stop` inside `next()` fails without the fix. The two existing cancel and callback-failure tests also assert the post-stop frame stays unfed.
7. **Tone.** Text-stream callback error propagates unchanged; cancel while the source is idle. The "source exception" line of its spec points to the shared runner's tests.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` (1017 passed, five runs, 4.1 to 4.4 s).
- The Pocket test fails with the fix reverted.
