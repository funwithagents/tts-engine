# Nested config + TTSEngine.from_config

**Status:** Done

Implements the configuration half of the `tts-engine` refactor: replaces the flat `{tts, audio, server}` config with the nested `{engine{module,player}, server, logging}` schema, models it as typed dataclasses (`AppConfig`, `TTSEngineConfig`, `PlayerConfig`, `LoggingConfig`), adds `TTSEngine.from_config(TTSEngineConfig)`, wires the logging level from config, and finishes the public API. Depends on [202608311000_rename-and-library-structure.md](202608311000_rename-and-library-structure.md) (the package is already `tts_engine` and the tools/mcp/logging structure is in place).

Implements the settled design in [configuration.md](../specs/configuration.md), [architecture.md](../specs/architecture.md) (`from_config`, public API), and the config-driven logging level in [project.md](../specs/project.md).

## Scope

- `src/tts_engine/config.py` — replace the flat dataclasses + tuple return with:
  - `PlayerConfig{ device }`, `LoggingConfig{ level="INFO" }`, keep `ServerConfig{ host, port }`, `TTSEngineConfig{ module: dict, player: PlayerConfig }`, `AppConfig{ engine, server, logging }`.
  - `load_config(path) -> AppConfig` parsing the nested schema; keep `ConfigError`.
- `src/tts_engine/engine.py` — add `@classmethod from_config(cls, config: TTSEngineConfig) -> TTSEngine`: `load_module(config.module)` + `AudioPlayer(config.player.device)`, then `cls(module, player)`. Keep `__init__(module, player)`.
- `src/tts_engine/cli.py` — rewire: `cfg = load_config(args.config)`; `setup_logging(cfg.logging.level)`; `engine = TTSEngine.from_config(cfg.engine)`; `create_server(engine)`; `uvicorn.run(..., host=cfg.server.host, port=cfg.server.port)`. Update the startup log line to the new fields.
- `src/tts_engine/__init__.py` — add `TTSEngineConfig` to the re-exports / `__all__`.
- `config.example.json` — rewrite to the nested schema ([configuration.md](../specs/configuration.md) "Example").
- `AGENTS.md` — update the "Config structure" JSON block (and any `tts.type`/`audio.device` prose) to the nested schema; note the `logging.level` field.
- `README.md` — update the config example to the nested schema.
- `tests/test_config.py` — rewrite around the nested schema and new validation: valid load returns `AppConfig`; extra `engine.module` fields preserved; missing `engine`; missing `engine.module`; `engine.module.type` missing/empty; invalid JSON; `server.port` out of range / zero; unknown `logging.level`; `server`/`logging` omitted → defaults.
- `tests/test_engine.py` — add a `from_config` test (patch `load_module` + `AudioPlayer`, assert the engine is built from the config's module/player); keep the existing injection-based `speak` tests.

## Steps

1. Rewrite `config.py` dataclasses and `load_config` for the nested schema; validate: `engine` required, `engine.module.type` non-empty string, `server.port` in 1–65535, `logging.level` a recognized level name; `server`/`logging` default when absent.
2. Add `TTSEngine.from_config` in `engine.py`.
3. Rewire `cli.py` to `AppConfig` + `from_config` + `setup_logging(cfg.logging.level)`.
4. Add `TTSEngineConfig` to `__init__.py` exports.
5. Rewrite `config.example.json`; update the `AGENTS.md` config block and `README.md` example.
6. Rewrite `tests/test_config.py`; extend `tests/test_engine.py` with the `from_config` test.
7. Run the verification gate.

## Verification

- `tests/test_config.py` exercises the new schema and every validation path; `tests/test_engine.py` covers `from_config` plus the existing `speak`/`drain` behavior; `config.example.json` parses under the new `load_config`.
- Gate: `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass.
- On success, promote to **Implemented** (here + [specs/_index.md](../specs/_index.md)): [configuration.md](../specs/configuration.md), [architecture.md](../specs/architecture.md), [project.md](../specs/project.md). With the previous plan's promotions, every spec is back to **Implemented**. Mark this plan `Done` in [_index.md](_index.md).
