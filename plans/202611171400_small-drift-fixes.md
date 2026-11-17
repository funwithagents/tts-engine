# Small drift fixes

**Status:** Done

Closes the smaller gaps from the repository consistency audit of 2026-11-16: places where a spec, a docstring, a dependency bound or a test claim had drifted from the code, plus the guards that keep two of them from coming back. No behavior of a working configuration changes.

## Scope

- `src/tts_engine/audition/page.html`, `src/tts_engine/audition/audition.py` — the page clamps its suggested `gain_db` to the field's range; `--target` is checked
- `src/tts_engine/modules/base.py`, `src/tts_engine/config.py` — an integer too large for a float is a `ConfigError`, not an `OverflowError`
- `src/tts_engine/modules/base.py` — `slice_text`'s docstring says what it does (a hard cut at the bound)
- `pyproject.toml`, `uv.lock` — `gradium>=0.6,<0.7`
- `tests/test_project_map.py` — statuses against the indexes; registered modules against the live table and the benchmark matrix
- `tests-e2e/test_modules.py` — every engine in `async with`
- `tests/test_config.py`, `tests/modules/test_tts_module_interface.py`, `tests/audition/test_audition.py` — tests for the above
- `specs/voice-audition.md`, `specs/tts-module-interface.md`, `specs/architecture.md`, `specs/project.md`, `AGENTS.md` — wording

## Steps

1. **Audition gain.** `page_data` carries `gain_db_min` / `gain_db_max` (`GAIN_DB_MIN` / `GAIN_DB_MAX`); the page's `levels()` clamps the rounded gain to them and marks a moved value `(limited)`, as `suggest_gain` does. `check_target` (finite, −60 to 0) runs at the start of `render` and in `page_data`, raising `AuditionError` (exit code 2).
2. **Overflow.** `_is_finite` in `base.py` and `config.py` wraps `math.isfinite` and returns False on `OverflowError`; `finite_number` and `_check_gain_db` use it.
3. **`slice_text`.** The code and its test already cut a long word at the bound; the docstring, the spec sentence and the test name said it passed whole. Fix the three.
4. **Gradium bound.** Add `<0.7` with its one-line reason (pre-1.0: the minor; validated against 0.6.4), per "Every provider extra carries an upper bound" in `specs/project.md`; `uv lock`.
5. **Public API inventories.** `architecture.md` and `project.md` list the three voice-listing names `__init__.py` already exports, and say where `ConfigError` and `TTSError` are imported from. The rename sentence in `project.md` is reworded: it described two successive renames and read as one reversed.
6. **Guards.** `test_spec_statuses_match_the_index`, `test_plan_statuses_match_the_index`, `test_every_registered_module_has_a_live_row_and_a_benchmark_entry` (the live table read with `ast`, since `tests-e2e/` is not importable from the unit tier).
7. **Live teardown.** The six engines of `tests-e2e/test_modules.py` are built in `async with`, so a failing assertion no longer leaves a client, model or socket open for the next parametrized case.

## Verification

- `test_page_carries_the_gain_range_and_refuses_a_target_outside_its_own`, `test_an_integer_too_large_for_a_float_is_a_config_error`, `test_finite_number_rejects_an_integer_too_large_for_a_float`, the three guards
- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest`
- `uv run pytest tests-e2e --collect-only` collects; the live tier itself was not run for this plan. The page's clamp is JavaScript and has no automated test: the unit test checks that the bounds reach the page and that the template uses them.
