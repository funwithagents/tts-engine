# Deepgram module

**Status:** Done

Implements [specs/modules/deepgram.md](../specs/modules/deepgram.md): an API-backed `deepgram` backend on Deepgram's Aura-2 voices over the `deepgram-sdk` 7.x speak v1 WebSocket. It sits behind a `deepgram` extra with a lazy import and streams `linear16` PCM with no conversion. Both input shapes are native over one socket, with pending-text backpressure (flushed segments, at most one unanswered `Flush`) and a closing handshake moved off the worker on abort. Tests are faked-SDK unit tests plus a live `MODULES` row gated on `DEEPGRAM_API_KEY`. Deliberately left out (the spec's deferrals): Flux TTS (`speak.v2`), `base_url` for self-hosted Deepgram, connection reuse, and configurable segment thresholds.

## Scope

- `src/tts_engine/modules/deepgram/__init__.py`: re-exports `DeepgramModule`.
- `src/tts_engine/modules/deepgram/module.py`: `DeepgramModule` (key resolution, validation, lazy import, the sender/reader/worker session, both synthesis methods).
- `src/tts_engine/modules/__init__.py`: import and `REGISTRY["deepgram"]`; the docstring lists the package.
- `pyproject.toml`: `deepgram = ["deepgram-sdk>=7.15,<8"]` extra, added to `all`; the two SDK-import `websockets` deprecation warnings filtered in pytest; the misplaced `mcp` comment moved back above the `mcp` extra; `uv.lock` refreshed.
- `tests/modules/test_deepgram.py`: unit tests against a faked `deepgram`.
- `tests/modules/test_lazy_imports.py`: poison `deepgram`, expect `deepgram` in the registry and its install hint.
- `tests-e2e/support.py`: `_REQUIRED_IMPORT["deepgram"] = "deepgram"` and a `MODULES` row.
- `examples/config.deepgram.json`: the example config.
- `.github/workflows/ci.yml`: map the optional `DEEPGRAM_API_KEY` secret in the live step.
- `specs/modules/deepgram.md`: the spec (written from the live probes recorded in its "Validated against" section).
- `specs/_index.md`: the intro names the Deepgram API; a `deepgram-module.md` row.
- `specs/tts-module-interface.md`: `deepgram/` in the package list, the `REGISTRY` snippet, the API-backed "Module kinds" row, the native-stream examples.
- `specs/architecture.md`: `deepgram/` in the diagram, the providers sentence, a `modules/deepgram/module.py` row in "Component responsibilities".
- `specs/project.md`: layout list, extras table, `all`, `deepgram` in the lazy-import guard's poisoned list, Intel macOS extras.
- `specs/testing.md`: the `deepgram` row in the `MODULES` paragraph and the `_REQUIRED_IMPORT` sentence.
- `specs/ci.md`: the `DEEPGRAM_API_KEY` secret and its expected skips.
- `README.md`: intro, install lines, module table row, a `### deepgram` section, examples list, the text-stream sentence, troubleshooting.
- `AGENTS.md`: `modules/deepgram/` in the layout row and the `modules/` map row.

## Steps

1. **Probe the API.** With `DEEPGRAM_API_KEY`, use throwaway scripts (not committed) to confirm the frame format, audio before `Flush`, the `Flush`/`Clear`/`Close` answers, handshake errors, the accepted `speed` and `sample_rate` values, the ~2000-character pending limit (`1008 DATA-0001`), the `EXCESSIVE_FLUSH` rate limit, and that flush-gated segments carry a fast 4500-character stream. Record the results in the spec.

2. **Packaging.** Add the `deepgram` extra after `mistral` and put it in `all`; `uv lock` and `uv sync --dev`.

3. **Module** (`modules/deepgram/module.py`, `__init__.py`).
   - Constants: `_DEFAULT_MODEL = "aura-2-thalia-en"`, `_SAMPLE_RATES`, `_SPEED_RANGE = (0.7, 1.5)`, `_SEGMENT_SOFT = 600`, `_SEGMENT_HARD = 900`, `_POLL_SECONDS = 0.1`.
   - `__init__`: lazy import first, then `_resolve_api_key` (the ElevenLabs rules, with messages naming "Deepgram module"), `model`, `sample_rate`, `speed`, `mip_opt_out`. Keep `self._connect_kwargs` (`model`, `encoding="linear16"`, `sample_rate`, plus the optional fields only when set) and build `DeepgramClient(api_key=key)`.
   - `_Session`: inbox queue, flush counters under a `Condition`, `close_sent`, sender errors, `done()`.
   - `_read` (reader thread), `_send` (sender thread: word grouping, `_cut` into segments, the flush gate, then the final `Flush` and `Close`), `_receive` (worker loop: 100 ms queue poll, odd-byte carry, end-of-utterance rules, `Warning` logging), `_synthesize` (open the socket inside the `TTSError` boundary, start the threads, close inline when done or on a daemon thread when aborted).
   - `synthesize_text` = `run_cancellable_worker` with `iter([text])`; `synthesize_text_stream` = `run_text_stream_worker` with the bridge's `pieces`.

4. **Registry.** Import `DeepgramModule`, add `"deepgram": DeepgramModule`, list `deepgram/` in the docstring.

5. **Unit tests** (`tests/modules/test_deepgram.py`). Fake `deepgram` and `deepgram.speak.v1.types` in `sys.modules` (a reactive fake socket, plus a scripted mode for server errors). Cover every bullet of the spec's "Testing → Unit" list. Stay within the 5-second budget: no sleeps beyond 50 ms settle checks, no network.

6. **Lazy-import guard.** Add `"deepgram"` to the poisoned names, `"deepgram"` to the expected registry set, and `("deepgram", {"type": "deepgram", "api_key": "k"})` to the construction loop.

7. **Live row.** Add `_REQUIRED_IMPORT["deepgram"] = "deepgram"`, a `_DEEPGRAM_CONFIG` (`api_key_env: "DEEPGRAM_API_KEY"`, `model: "aura-2-thalia-en"`) and `pytest.param("deepgram", _DEEPGRAM_CONFIG, id="deepgram")`. Add `DEEPGRAM_API_KEY: ${{ secrets.DEEPGRAM_API_KEY }}` to the CI live step.

8. **Example and docs.** `examples/config.deepgram.json`; the README, AGENTS.md and cross-spec edits listed in Scope.

9. **Statuses.** Spec to `Implemented` (file and index), this plan to `Done` (here and in [_index.md](_index.md)).

## Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`: clean.
- `uv run pytest`: the new unit tests and the lazy-import guard pass, and the tier stays within its budget.
- `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k deepgram -o log_cli=true --log-cli-level=INFO'`: every conformance scenario passes against the real API, and both cancel scenarios surface in about 0.05 s.
- A fast ~2900-character text stream through `synthesize_text_stream` completes without `DATA-0001` (measured: 170 s of audio).
- Then mark this plan `Done` and the spec `Implemented`, in the files and both indexes.
