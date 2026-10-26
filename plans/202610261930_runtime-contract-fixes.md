# Runtime contract fixes

**Status:** Done

Closes the seven contract violations found by the 2026-10-26 repository consistency review, each against behaviour its spec already settles: [tts-module-interface.md](../specs/tts-module-interface.md) ("Cancellation", "Text-stream helpers"), [mcp-server.md](../specs/mcp-server.md) ("Transport"), [gradium.md](../specs/modules/gradium.md) ("Cancellation"), [elevenlabs.md](../specs/modules/elevenlabs.md) ("Text stream"), [pocket.md](../specs/modules/pocket.md) ("Error handling") and the validation sections of the tone, audiofile, pocket, gradium, deepgram and cartesia specs. One plan rather than seven because every fix is small, independent and verified the same way; the specs only gain the clauses that pin the fixed behaviour down. Deliberately left out: the review's design items (bounded text-stream backpressure, drain off the event loop, shared validation/credential helpers, UTF-8 config loading) and its documentation restructuring — those need their own spec work first.

## Scope

- `src/tts_engine/modules/base.py` — `run_cancellable_worker` / `run_text_stream_worker` keep waiting for the worker thread through repeated cancellation; a `CancelledError` raised by the text source is a source failure, not success.
- `src/tts_engine/mcp.py`, `src/tts_engine/mcp_server_cli.py` — `create_server(engine, *, host, port)` hands the bind address to `FastMCP`, so its Host-header policy follows the deployment address.
- `src/tts_engine/modules/gradium/module.py` — the receive loop polls `stop` on a timer while the server is silent, keeping the pending receive across polls.
- `src/tts_engine/modules/elevenlabs/module.py` — an empty text stream opens no connection; a raw source that yields no audio builds no decoder; decoder construction sits inside the raw-source cleanup.
- `src/tts_engine/modules/pocket/module.py` — a callback failure sets the library's `stop` event before propagating; `device` must be a string.
- `src/tts_engine/modules/tone/module.py` — numeric fields must be finite.
- `src/tts_engine/modules/audiofile/module.py` — a truncated/empty WAV (`EOFError`) is a `ConfigError` at construction and a `TTSError` at stream time.
- `src/tts_engine/modules/{gradium,deepgram,cartesia}/module.py` — `sample_rate` must be an `int`, not merely equal to one.
- `tests/modules/test_tts_module_interface.py`, `tests/test_mcp.py`, `tests/test_mcp_server_cli.py`, `tests/modules/test_{gradium,elevenlabs,pocket,tone,audiofile,deepgram,cartesia}.py` — one regression test per fix.
- The specs named above — the clauses that make each fixed behaviour explicit, and their unit-test inventories.

## Steps

1. **Repeated cancellation** (`base.py`). Add a private `_join(task)` that waits for a task to finish, absorbing further `CancelledError`s and re-raising one afterwards. Use it wherever the helpers wait for the worker thread or the pump after a cancel. Test: cancel a worker that ignores `stop` for a while twice; the coroutine must not raise until the thread has exited.
2. **Source cancellation** (`base.py`). In the pump, treat a `CancelledError` the pump did not ask for (`current_task().cancelling() == 0`) like any other source exception: record it, set `stop`, raise it once the thread has exited. Test: a source that yields then raises `CancelledError` makes the bridge raise `CancelledError` after the worker exited, not return.
3. **MCP host policy** (`mcp.py`, CLI). `create_server(engine, *, host="127.0.0.1", port=8000)` builds `FastMCP("tts-engine", host=host, port=port)`: the SDK enables DNS-rebinding protection (Host allow-list) for a loopback bind only. The CLI passes the configured address. Test with real in-process HTTP requests: a loopback bind answers `localhost` and rejects a LAN Host (421); a `0.0.0.0` bind answers the LAN Host.
4. **Gradium silent-server cancellation** (`gradium/module.py`). Keep the pending `__anext__` future across loop iterations and wait on it (plus the sender while it runs) with a 100 ms timeout, re-checking `stop` on each wake. Test: a fake server that sends one audio message then nothing; a cancel of `synthesize_text` propagates promptly and closes the connection.
5. **ElevenLabs empty input** (`elevenlabs/module.py`). The stream worker peeks the first piece and returns without connecting when there is none. `_decode_and_feed` opens the raw source inside the `try/finally` that closes it, peeks the first raw chunk and returns before building a decoder when there is none. Tests: an empty text stream never connects and feeds nothing; a provider stream with no chunks succeeds with the real decoder unpatched.
6. **Pocket callback failure** (`pocket/module.py`). Wrap the feed loop: on any exception set `stop` (so the library's generation thread halts as it does on cancel) and re-raise unchanged. Test: a raising callback leaves the library's `stop` event set and the callback's exception intact.
7. **Validation** (seven modules). `sample_rate` requires `isinstance(int)` in gradium/deepgram/cartesia; tone's `_positive_number` rejects non-finite values; pocket's `device` must be a string; audiofile catches `EOFError` alongside `OSError`/`wave.Error`. Tests: one parametrized row each (`48000.0`, `nan`/`inf`, `[]`, an empty `.wav`).
8. **Specs.** Add the clauses above to the governing specs and extend their unit-test inventories; statuses stay `Implemented` (the code matches again in the same change).

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` — all green, with the new tests failing before their fix and passing after. The live tier is not required for these changes (every fix is reproduced with fakes), but `tests-e2e/` should still pass for `tone`/`audiofile` and any provider with a key present.
