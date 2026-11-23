# Setup: the contract's names on the modules

**Status:** Done

Implements the changes to `specs/setup.md` that followed the contract's revision (module-setup, "Contract: explicit names, check_setup covers extras and keys"): the seven functions take their new names, `TTSModule` carries one method per contract function with a default, and `check_setup` reports the module's extra and its key variables before its files.

## Scope

- `src/tts_engine/setup.py` — `config_schema`, `required_extras`, `check_config`, `check_setup`; each the engine's part plus the selected module's method of the same name
- `src/tts_engine/modules/base.py` — `required_extras`, `check_config` (was `problems`), `check_setup`, `prepare`, `options` with defaults; `imports`, `check_files` (was `items`), `config_rules` (was `check_config`) underneath
- every `src/tts_engine/modules/<name>/module.py` — `imports()` holding the provider imports, called by the constructor; the renamed hooks
- `tests/test_setup.py`, `tests/modules/test_{piper,pocket,phonon,qwen3}_setup.py` — the new names; the extra and key items
- `specs/setup.md`, `docs/setup.md`, `AGENTS.md`, the indexes

## Steps

1. Rename the functions of `tts_engine.setup` and the module members.
2. Give each module with an extra an `imports()` classmethod holding its provider imports and install hint; its constructor calls it in place of its own `try`.
3. `TTSModule.check_setup`: the extra item from `imports`, the key items from the `secret="env"` fields and the environment, then `check_files` when the extra imports.
4. `TTSModule.options`: `"voices"` from `list_voices`; `tts_engine.setup.options` keeps `"output-devices"` and delegates the rest.
5. Tests for the extra and key items; the no-provider subprocess checks every module reports its extra missing with its constructor's hint.

## Deviation from the analysis

That every registered module declares `title`, `extra` and `fields` stays checked by a test, not in `__init_subclass__`: tests and applications write stub modules for the engine that never go through `setup`, and would have to declare them for nothing.

## Verification

`uv run ruff check .`, `uv run ruff format src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` all pass.
