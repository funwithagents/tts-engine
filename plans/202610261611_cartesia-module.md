# Cartesia module

**Status:** Done

Implements [specs/modules/cartesia.md](../specs/modules/cartesia.md): an API-backed `cartesia` backend on Cartesia's Sonic models through the `cartesia` 4.x SDK's sync TTS WebSocket, behind a `cartesia` extra with a lazy import, streaming `pcm_s16le` with no conversion, a native text stream for both input shapes (word groups pushed to a context, idle rotation to a fresh context before the server's ~7 s expiry, `cancel` before every close), faked-SDK unit tests and a live `MODULES` row gated on `CARTESIA_API_KEY`. Deliberately leaves out the spec's deferrals: connection reuse, timestamps, a custom endpoint.

## Scope

- `src/tts_engine/modules/cartesia/__init__.py` — re-exports `CartesiaModule`.
- `src/tts_engine/modules/cartesia/module.py` — `CartesiaModule`: key resolution, validation, lazy `from cartesia import Cartesia`, the sender/worker pair, rotation, both synthesis methods.
- `src/tts_engine/modules/__init__.py` — import and `REGISTRY["cartesia"]`; docstring lists the package.
- `pyproject.toml` — `cartesia = ["cartesia[websockets]>=4.2,<5"]` extra, added to `all`; `uv.lock` refreshed (`websockets` 16.0 → 15.0.1 under the SDK's `<16` cap).
- `tests/modules/test_cartesia.py` — unit tests against a faked `cartesia`.
- `tests/modules/test_lazy_imports.py` — poison `cartesia`, expect `cartesia` in the registry and its install hint.
- `tests-e2e/support.py` — `_REQUIRED_IMPORT["cartesia"] = "cartesia"` and a `MODULES` row.
- `examples/config.cartesia.json` — the spec's example config.
- `.github/workflows/ci.yml` — map the optional `CARTESIA_API_KEY` secret in the live step.
- `specs/modules/cartesia.md` — status to `Implemented`.
- `specs/_index.md` — intro names the Cartesia API; a `cartesia-module.md` row.
- `specs/tts-module-interface.md` — `cartesia/` in the package list, the `REGISTRY` snippet, the API-backed "Module kinds" row, the native-backend sentence.
- `specs/architecture.md` — `cartesia/` in the diagram, the providers sentence, a `modules/cartesia/module.py` row in "Component responsibilities".
- `specs/project.md` — layout list and extras table, `cartesia` in the lazy-import guard's poisoned list, Intel macOS extras.
- `specs/testing.md` — the `cartesia` row in the `MODULES` paragraph and the `_REQUIRED_IMPORT` sentence.
- `specs/ci.md` — the `CARTESIA_API_KEY` secret and its expected skips.
- `README.md` — intro, install lines, module table row, a `### cartesia` section, examples list, the text-stream sentence, troubleshooting.
- `AGENTS.md` — `modules/cartesia/` in the layout row and the `modules/` map row.
- `analysis/tts-backend-candidates.md` — row 4 marked integrated (local only: `analysis/` is not committed).

## Steps

1. **Packaging.** Add the `cartesia` extra after `gemini` and put it in `all`; `uv lock` and `uv sync --dev`. Confirm `from cartesia import Cartesia` and `websockets.sync.client` import, and that the deepgram tests still pass on websockets 15.

2. **Module** (`modules/cartesia/module.py`, `__init__.py`).
   - Constants: `_DEFAULT_MODEL = "sonic-3.6"`, `_SAMPLE_RATES`, the `speed`/`volume`/`max_buffer_delay_ms` ranges, `_POLL_SECONDS = 0.1`, `_ROTATE_MIN = 4.0`, `_EXPIRY_MARGIN = 1.0`, `_DEFAULT_BUFFER_DELAY = 3.0`.
   - `__init__`: lazy import first, then `_resolve_api_key`, `voice_id`, `model`, `sample_rate`, optional fields. Keep the context kwargs (`model_id`, `voice`, `output_format`, plus `language` / `generation_config` / `max_buffer_delay_ms` only when set) and `self._rotate_after`. Build `Cartesia(api_key=key)`.
   - `_Session`: lock-protected `last_activity` (monotonic), the list of opened context ids, `current` id, `pushed` (current context has text), `expired` ids, `final` id, `sender_done`, `sender_errors`.
   - Sender: for each piece, `split_at_last_space`; `push(words)` when non-empty. `push` first rotates when `expired` holds the current id or `now - last_activity >= rotate_after` with the current context `pushed`. At end of stream: push the remainder; if the current context is `pushed` and not idle/expired → `no_more_inputs()`, set `final`. Stop sending as soon as `stop` is set. Errors go to `sender_errors` unless `stop` is set.
   - Worker `_receive`: loop while not `stop`: raise a sender error as `TTSError`; return when `sender_done` and `final is None`; `recv_bytes(timeout=0.1)` (`TimeoutError` → continue; other exceptions → `TTSError`); dispatch `chunk` (decode, odd-byte carry, refresh `last_activity`, `callback` outside the `try`), `done` (return if `final`, else mark expired), `error` (current context → `TTSError`, else `log.debug`).
   - `_synthesize`: connect inside the provider `try`, open the first context, start the sender daemon, run `_receive`; `finally`: set `stop`, `cancel` every context (ignore failures), close the connection.
   - `synthesize_text` / `synthesize_text_stream` as in the Deepgram module.

3. **Registry.** Import `CartesiaModule`, add `"cartesia": CartesiaModule`, list `cartesia/` in the docstring.

4. **Unit tests** (`tests/modules/test_cartesia.py`). Fake `cartesia` in `sys.modules` as the spec's "Testing → Unit" describes; cover every bullet. Patch `_ROTATE_MIN`-derived `rotate_after` small for the rotation tests. Keep to the 5-second budget.

5. **Lazy-import guard.** `"cartesia"` in the poisoned names, `"cartesia"` in the expected registry set, `("cartesia", {"type": "cartesia", "api_key": "k", "voice_id": "v"})` in the construction loop.

6. **Live row.** `_REQUIRED_IMPORT["cartesia"] = "cartesia"`, a `_CARTESIA_CONFIG` (`api_key_env: "CARTESIA_API_KEY"`, the Tessa voice id) and `pytest.param("cartesia", _CARTESIA_CONFIG, id="cartesia")`. Add `CARTESIA_API_KEY: ${{ secrets.CARTESIA_API_KEY }}` to the CI live step.

7. **Example and docs.** `examples/config.cartesia.json`; the README, AGENTS.md and cross-spec edits listed in Scope.

8. **Statuses.** Spec to `Implemented` (file and index), this plan to `Done` (here and in [_index.md](_index.md)).

## Deviations found while building

- **Receive flow control off.** Measured live with a real-time callback, a cancel took the 10 s close timeout: unread audio filled websockets' 16-frame queue and hid the server's close frame. The socket is opened with `websocket_connection_options={"max_queue": None}`.
- **Close off the worker, always.** The server holds the close handshake for ~1 s in many cases even after `done` and `cancel`, so every socket is closed on a daemon thread once callbacks have stopped (as Deepgram does for aborted sockets). `say` returns ~0.18 s after its last chunk; cancels surface in 0.05–0.19 s.
- **An `error` on an expired context is ignored**, like one on a context already left behind.

## Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright` — clean.
- `uv run pytest` — new unit tests, the deepgram tests on websockets 15 and the lazy-import guard pass; the tier stays within its budget.
- `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k "cartesia or deepgram or gemini"'` — every conformance scenario passes against the real APIs.
- A live check of rotation: a text stream with a 10 s pause mid-answer speaks the text after the pause.
- Then mark this plan `Done` and the spec `Implemented`, in the files and both indexes.
