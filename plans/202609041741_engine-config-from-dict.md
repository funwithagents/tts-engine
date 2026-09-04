# TTSEngineConfig.from_dict constructor

**Status:** Done

Implements the settled behavior in [configuration.md](../specs/configuration.md) ("`TTSEngineConfig.from_dict(engine_block)`"): extract the `engine`-block parsing/validation out of `load_config` into a named `TTSEngineConfig.from_dict` classmethod, so a library caller can build a validated engine config from an in-memory dict without a temp file or duplicated validation. `load_config` is reimplemented in terms of it; file-level and `server` validation stay in `load_config`.

## Scope

- `src/tts_engine/config.py` — add `TTSEngineConfig.from_dict(engine_block)` carrying the engine-block validation (engine/module objects, non-empty string `module.type`, `player.device` a str|int-not-bool|None); `load_config` delegates the `engine` block to it.
- `tests/test_config.py` — functional tests driving `from_dict` directly (valid block, verbatim module carry-through, no env read, invalid shapes) plus a `load_config == from_dict` parity test.
- `specs/configuration.md` — document `from_dict` as the sanctioned in-memory constructor; note `load_config` is implemented in terms of it and the engine-block rules apply to both entry points. Status → `Updated` while code lags, back to `Implemented` when done.
- `specs/_index.md` — mirror the status.

## Steps

1. Add `from_dict` to `TTSEngineConfig`, moving the inline `engine`-block checks from `load_config` into it and adding the `player.device` type check the spec's validation rules already require.
2. Rewrite `load_config` to call `TTSEngineConfig.from_dict(data["engine"])` after the `"engine" in data` presence check; leave JSON/file and `server` handling unchanged.
3. Add the `from_dict` tests and the parity test.
4. Update the spec + index; flip status to `Implemented` once verified.

## Verification

`uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. `load_config` produces identical results for valid configs (parity test); invalid `player.device` values now raise `ConfigError` as the spec's validation rules always specified. Mark `Done` here and in [_index.md](_index.md); return [configuration.md](../specs/configuration.md) to `Implemented`.
