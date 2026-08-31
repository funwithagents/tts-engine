# Config-only TTSEngine constructor

**Status:** Done

Implements the revised behavior in [architecture.md](../specs/architecture.md) ("`TTSEngine` construction"): collapse the engine's two constructors into a single `__init__(config: TTSEngineConfig)` that builds the module and player from config, and remove the `from_config` classmethod and the `__init__(module, player)` injection overload.

## Scope

- `src/tts_engine/engine.py` — `__init__` now takes `TTSEngineConfig` and does the `load_module` + `AudioPlayer` wiring the old `from_config` did; `from_config` classmethod removed; drop the now-unused `TTSModule` import.
- `src/tts_engine/cli.py` — `TTSEngine.from_config(cfg.engine)` → `TTSEngine(cfg.engine)`.
- `tests/test_engine.py` — the `engine` fixture patches `tts_engine.engine.load_module` + `AudioPlayer` to inject fakes through the constructor; the wiring test constructs `TTSEngine(cfg)` directly.
- `tests-e2e/test_engine.py` — library path calls `TTSEngine(app_config.engine)`.
- Specs/docs reconciled to the single constructor: [architecture.md](../specs/architecture.md), [configuration.md](../specs/configuration.md), [overview.md](../specs/overview.md), [audio-player.md](../specs/audio-player.md), [mcp-server.md](../specs/mcp-server.md), [project.md](../specs/project.md), [testing.md](../specs/testing.md), [specs/_index.md](../specs/_index.md), plus `AGENTS.md` and `README.md`.

## Steps

1. Rewrite `engine.py` so `__init__(config)` performs the module/player construction; delete `from_config`.
2. Update `cli.py` and both `test_engine.py` files to the new call site.
3. Rework the unit-test `engine` fixture to patch `load_module`/`AudioPlayer` for DI; rename the wiring test.
4. Reconcile every spec/doc reference to `from_config` (dependency injection is now at the module boundary, not the constructor signature).

## Verification

`uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass (47 tests). Mark `Done` here and in [_index.md](_index.md); [architecture.md](../specs/architecture.md) stays `Implemented` (code matches the revised design).
