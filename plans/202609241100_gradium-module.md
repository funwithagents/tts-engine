# Gradium module

**Status:** Done

Implements [specs/gradium-module.md](../specs/modules/gradium.md) in full: the `gradium` provider module behind a `gradium` extra, registered, with an example config, faked-SDK unit tests and a live `MODULES` row. It deliberately leaves out incremental text input and connection reuse (the spec's deferrals).

## Scope

- `pyproject.toml` — `gradium = ["gradium>=0.6"]` extra; add it to `all`.
- `src/tts_engine/modules/gradium.py` — new `GradiumModule`: lazy import, config validation, key resolution, `tts_realtime` consumer on a private event loop inside `run_cancellable_worker`.
- `src/tts_engine/modules/__init__.py` — `REGISTRY["gradium"] = GradiumModule`.
- `examples/config.gradium.json` — complete example config (`api_key_env`, Alex voice, defaults spelled out).
- `tests/modules/test_gradium.py` — unit tests against a faked `gradium` SDK.
- `tests/modules/test_lazy_imports.py` — poison `gradium`/`aiohttp`, expect the registry to include `gradium`, construction to raise the install hint.
- `tests-e2e/support.py` — `_GRADIUM_CONFIG` row in `MODULES`, `_REQUIRED_IMPORT["gradium"] = "gradium"`.
- `specs/_index.md`, `specs/tts-module-interface.md`, `specs/project.md`, `specs/testing.md`, `AGENTS.md`, `plans/_index.md` — registry/extras/MODULES/module-map mentions (done alongside the spec).

## Steps

1. **Extra.** Add the `gradium` extra to `pyproject.toml` and to `all`; `uv sync --dev` to pull it into the dev environment.
2. **Module.** Write `modules/gradium.py`:
   - `__init__`: lazy `from gradium import GradiumClient` → `ConfigError` hint; resolve the key (`api_key` / `api_key_env`, ElevenLabs rules); validate `voice_id`, `model`, `sample_rate` (allowed set, no bools), `temp`/`cfg_coef`/`padding_bonus` (numeric, ranges, no bools), `rewrite_rules`/`pronunciation_id`/`base_url` (non-empty strings); build `self._json_config` from only the voice settings that were set; build `GradiumClient(api_key=..., base_url=...)` (base_url only when set).
   - `sample_rate` property returns the configured rate.
   - `stream()`: `run_cancellable_worker(lambda stop: asyncio.run(self._consume(text, callback, stop)))`. `_consume` opens `tts_realtime(wait_for_ready_on_start=True, model_name=..., voice_id=..., output_format=f"pcm_{rate}", json_config=... if any, pronunciation_id=... if set)`, checks `tts.ready["sample_rate"]` against the declared rate, sends the text + EOS, then iterates: poll `stop`, wrap SDK `__anext__` in the `TTSError` boundary, call `callback(msg["audio"])` outside it for `"audio"` messages.
3. **Registry + example.** Register `"gradium"`; add `examples/config.gradium.json`.
4. **Unit tests.** `tests/modules/test_gradium.py` with a fake `gradium` module injected via `monkeypatch.setitem(sys.modules, ...)`: a `FakeClient` recording constructor kwargs and `tts_realtime` kwargs, returning a scripted `FakeTts` (async context manager + async iterator over a list of messages, `ready` dict, records `send_text`/`send_eos`, and `closed` flag). Cover the cases listed in the spec's "Testing" section, including the cancellation test (an endless fake iterator with a small sleep, same shape as ElevenLabs's).
5. **Guards + live row.** Update `test_lazy_imports.py`; add the `MODULES` row and `_REQUIRED_IMPORT` entry in `tests-e2e/support.py`.

## Verification

- `uv run ruff check .`, `uv run ruff format .`, `uv run pyright` — clean.
- `uv run pytest` — the new unit file passes and the suite stays under the 5-second budget.
- `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k gradium'` — both conformance scenarios pass against the real API (the key is in `~/.zshrc`).
- Then flip this plan to `Done` (here and in `_index.md`) and the spec to `Implemented` (there and in `specs/_index.md`).
