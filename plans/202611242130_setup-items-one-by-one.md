# Setup: items one by one, required or not

**Status:** Done

Implements the changes to `specs/setup.md` ("`check_setup` and `prepare`") that followed the contract's revision in app-setup (the package renamed from module-setup, then "Items one by one: required or not, each prepared on its own, with progress"): every `check_setup` item says whether a session needs it, and `prepare` makes one item ready per call, safely alongside another, its progress measured where a hook exists.

## Scope

- `pyproject.toml`, `uv.lock` — the dev dependency `module-setup` renamed `app-setup`, its git source moved to the new repository, the lock pinning its `main`
- `src/tts_engine/setup.py` — `prepare(config, item, *, on_progress, base_dir)`
- `src/tts_engine/modules/base.py` — `required` on the extra and key items; `TTSModule.prepare` over one item (imports, lookup, `ValueError`, `fetch`, `1.0`); the `fetch` hook; `hub_download` and the `tqdm` stand-in measuring a Hub download
- `src/tts_engine/modules/{kokoro,piper,qwen3,pocket,phonon}/module.py` — `required` on every file item; `fetch` of one item in place of `prepare` of all; phonon's loads under a lock
- `tests/test_setup.py`, `tests/modules/test_{piper,pocket,phonon,qwen3}_setup.py` — `app_setup`; the six keys on every item; one item prepared alone, two at the same time, progress fractions, a failure, an unknown id
- `specs/setup.md`, `specs/project.md`, `docs/setup.md`, `AGENTS.md`, the indexes

## Steps

1. Rename the dependency and relock.
2. `required` on every item: `true` for the extra, the keys and a local file; `false` for every file the constructor fetches itself.
3. `TTSModule.prepare(config, item, on_progress)`: the extra's imports, the item among `check_files` (`ValueError` naming it otherwise), `fetch` unless ready, then `1.0`; `setup.prepare` hands it the module block.
4. `hub_download`: `hf_hub_download` with a `tqdm_class` turning bytes into fractions; kokoro, piper, qwen3 and pocket's `hf://` files go through it.
5. phonon's `fetch` loads the model (with the voice for the voice item) under a module lock.
6. Tests, spec, docs.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks examples`, `uv run pyright`, `uv run pytest` all pass.
