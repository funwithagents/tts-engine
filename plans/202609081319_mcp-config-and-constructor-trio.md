# MCPServerConfig rename + constructor trio

**Status:** Done

Implements the reworked constructor surface in [configuration.md](../specs/configuration.md) ("Top-level structure", "Constructors"): rename `AppConfig` → `MCPServerConfig` (the top-level `{engine, server}` block is the MCP server's config, not a generic "app"), replace the free `load_config(path)` function with `MCPServerConfig.from_json_file(path)`, and give both public config dataclasses a symmetric `from_dict` / `from_json` / `from_json_file` trio layered file → json → dict. Deliberately leaves validation rules, error messages (path-in-message for file parses), and the `engine` block contract unchanged.

## Scope

- `src/tts_engine/config.py` — rename `AppConfig` → `MCPServerConfig`; move `load_config`'s parsing/validation into `MCPServerConfig.from_dict`; add `MCPServerConfig.from_json` / `from_json_file` and `TTSEngineConfig.from_json` / `from_json_file`; add a module-level `_loads(text, source=None)` JSON helper that wraps `JSONDecodeError` as `ConfigError` (with the path when parsing a file); delete `load_config`.
- `src/tts_engine/mcp_server_cli.py` — import `MCPServerConfig`; `cfg = MCPServerConfig.from_json_file(args.config)`.
- `src/tts_engine/__init__.py` — re-export `MCPServerConfig` in place of `load_config` (update `__all__`).
- `tests/test_config.py` — rework around `MCPServerConfig.from_json_file` / `from_json` / `from_dict` and the two new `TTSEngineConfig` JSON constructors; keep every existing validation and parity case.
- `tests/test_mcp_server_cli.py` — swap `load_config` for `MCPServerConfig.from_json_file`.
- `specs/configuration.md` — document `MCPServerConfig` and the constructor trio on both configs; status → `Updated`, back to `Implemented` when done.
- `specs/overview.md`, `specs/architecture.md`, `specs/project.md`, `README.md` — update `AppConfig`/`load_config` references to `MCPServerConfig`/`from_json_file`.

## Steps

1. In `config.py`: add module-level `_loads(text, source=None)` that does `json.loads` and re-raises `JSONDecodeError` as `ConfigError` (message includes `in {source}` when given).
2. Add `TTSEngineConfig.from_json(text)` → `from_dict(_loads(text))` and `from_json_file(path)` → reads the file, `from_dict(_loads(text, source=path))`. The file's top-level object **is** the engine block (module + player).
3. Rename `AppConfig` → `MCPServerConfig`; add `MCPServerConfig.from_dict(data)` carrying the current `load_config` body after the JSON parse (require `engine`, delegate to `TTSEngineConfig.from_dict`, validate `server`/`port`); add `from_json` / `from_json_file` mirroring step 2.
4. Delete `load_config`. Update `mcp_server_cli.py` and `__init__.py` accordingly.
5. Rework `tests/test_config.py` and `tests/test_mcp_server_cli.py`; add cases for the two new JSON constructors on each config (valid parse + invalid-JSON path in message).
6. Update `configuration.md` (+ overview/architecture/project/README) prose and code blocks.

## Verification

`uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. New/updated `test_config.py` covers `MCPConfig.from_dict/from_json/from_json_file`, `TTSEngineConfig.from_json/from_json_file`, unchanged validation paths, and the `from_json_file == from_dict` parity. Mark `Done` here and in [_index.md](_index.md); return [configuration.md](../specs/configuration.md) to `Implemented`.
