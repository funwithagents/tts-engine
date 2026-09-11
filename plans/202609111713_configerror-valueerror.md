# `ConfigError` subclasses `ValueError`

**Status:** Done

Implements the type contract in `specs/configuration.md` ("Validation rules"): `ConfigError` subclasses `ValueError`, so a caller composing this engine's config with others can catch one uniform "bad config" type. Purely additive to the type hierarchy — no `raise ConfigError(...)` site or message changes, no new re-export, and `MCPServerConfig`, the modules, and the constructor trio are untouched.

## Scope

- `src/tts_engine/config.py` — `class ConfigError(Exception)` → `class ConfigError(ValueError)`, with a docstring stating why
- `tests/test_config.py` — a test pinning the contract (`issubclass` + a real validation failure caught as `ValueError`)
- `specs/configuration.md` — a "Validation rules" bullet documenting the type contract

## Steps

1. Change the base class of `ConfigError` to `ValueError` and document the rationale in its docstring.
2. Confirm nothing relies on `ConfigError` *not* being a `ValueError`: `src/` has no `except ValueError` (its handlers catch `JSONDecodeError`, `ImportError`, `StopIteration`, `CancelledError`, or broad `Exception`), so no existing handler newly swallows or re-wraps a `ConfigError`.
3. Add `test_config_error_is_a_value_error` to `tests/test_config.py`.
4. Add the type-contract bullet to `specs/configuration.md`. The spec stays `Implemented` — spec and code move together in this change, so the code still matches; no status flip is required.

## Verification

- `uv run ruff check .` — clean
- `uv run pyright` — 0 errors
- `uv run pytest` — full unit tier green, including every existing `pytest.raises(ConfigError, ...)` assertion and the new test
