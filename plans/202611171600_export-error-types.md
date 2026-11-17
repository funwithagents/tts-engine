# Export the error types

**Status:** Done

Implements "Public API" in `specs/architecture.md`: `ConfigError` and `TTSError`, the two exception types a caller handles, are re-exported from `tts_engine`. Their definitions do not move, so the submodule paths keep working.

## Scope

- `src/tts_engine/__init__.py` — import both, add them to `__all__`
- `tests/test_engine.py` — the top-level names are the same classes
- `docs/streaming.md` — the error-handling example imports them from `tts_engine`
- `specs/architecture.md`, `specs/project.md`, `AGENTS.md` — the inventories

## Steps

1. Add `ConfigError` (from `tts_engine.config`) and `TTSError` (from `tts_engine.modules.base`) to the imports and `__all__` of `__init__.py`. Both modules are already imported there, so nothing new is loaded and no extra is needed.
2. Update the docs example, which `tests/test_docs_examples.py` executes, and drop the sentence saying they are not re-exported.

## Verification

- `test_the_error_types_a_caller_handles_are_top_level_exports`, `test_errors_and_cancellation`
- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest`
