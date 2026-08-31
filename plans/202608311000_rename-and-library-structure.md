# Rename to tts-engine + library structure

**Status:** Done

Implements the structural half of the `tts-engine` refactor: renames the package `tts_mcp` → `tts_engine` (dist `tts-engine`, entry point `tts-engine-mcp`), splits the MCP server into a thin transport layer over a new provider-agnostic **tools** layer (`server.py` → `mcp.py` + `tools.py`), makes logging use the package logger instead of the root logger, and exposes a curated public API. It deliberately **does not** change the config schema or add `TTSEngine.from_config` — that is the next plan ([202608311001_nested-config-and-from-config.md](202608311001_nested-config-and-from-config.md)). Behavior is otherwise identical; the config file it reads is still the old `{tts, audio, server}` shape after this plan.

Implements the settled design in [project.md](../specs/project.md) (rename, entry point, layout, package logger), [tools.md](../specs/tools.md), [mcp-server.md](../specs/mcp-server.md) ("thin wrappers over the tools layer"), and the tools layer in [architecture.md](../specs/architecture.md).

## Scope

- `pyproject.toml` — `[project].name` → `tts-engine`; `[project.scripts]` → `tts-engine-mcp = "tts_engine.cli:main"`; description reworded (library + MCP).
- `src/tts_mcp/` → `src/tts_engine/` — `git mv` the whole package (incl. `modules/`); fix every intra-package import.
- `src/tts_engine/server.py` → `src/tts_engine/mcp.py` — renamed; `create_server` now delegates to `tools.speak`; `FastMCP("tts-engine")`.
- `src/tts_engine/tools.py` — **new**: `async def speak(engine, text) -> str` with the empty-text guard and `TTSError` → string mapping moved out of the server.
- `src/tts_engine/_logging.py` — `setup_logging(level="INFO")` configures the package logger `logging.getLogger("tts_engine")` (StreamHandler + level); no `basicConfig`, never the root logger.
- `src/tts_engine/cli.py` — imports from `tts_engine`; builds the server from `tts_engine.mcp.create_server`. Wiring stays manual (`load_module` + `AudioPlayer` + `TTSEngine(module, player)`) — `from_config` lands in the next plan. Still calls `setup_logging()` (default level).
- `src/tts_engine/__init__.py` — re-export `TTSEngine` and `load_config` (public API; `TTSEngineConfig` is added in the next plan when it exists).
- `tests/` + `tests-e2e/` — `tts_mcp` → `tts_engine` in every import.
- `tests/test_server.py` → `tests/test_mcp.py` — retarget to `tts_engine.mcp`; assert the wrapper delegates to / returns `tools.speak`'s result.
- `tests/test_tools.py` — **new**: functional tests for `tools.speak` (empty text → error string; success → `"OK"`; `TTSError` → `"TTS error: <message>"`).
- `tests/test_project_map.py` — `_PKG` → `src/tts_engine`; `_MODULE_LINK` regex anchor → `src/tts_engine/`.
- `AGENTS.md` — rename all paths/names; framing to library-first; project map (add `tools.py`, `mcp.py`; drop `server.py`); entry points; repo layout tree; data-flow diagram (insert the tools layer); "Adding a new TTS module" paths. **Leave the "Config structure" JSON block as-is** — the schema changes in the next plan.
- `README.md` — rename, and document library use (`import tts_engine`) alongside the MCP entry point. (Config example stays old-shape until the next plan.)
- `config.example.json` — unchanged (still valid old schema after this plan).

## Steps

1. `git mv src/tts_mcp src/tts_engine`, then `git mv src/tts_engine/server.py src/tts_engine/mcp.py`.
2. Update `pyproject.toml`: `name = "tts-engine"`, script `tts-engine-mcp = "tts_engine.cli:main"`, reword description.
3. Create `tools.py` with `speak(engine, text)` per [tools.md](../specs/tools.md): empty-text guard returns `"TTS error: text must not be empty"`; else `await engine.speak(text)` and return `"OK"`; catch `TTSError`, `log.error`, return `"TTS error: <e>"`.
4. Rewrite `mcp.py`'s `speak` tool to `return await tools.speak(engine, text)`; rename the app to `FastMCP("tts-engine")`.
5. Rewrite `_logging.py`: `setup_logging(level="INFO")` gets `logging.getLogger("tts_engine")`, sets its level, attaches a `StreamHandler` with the existing format, sets `propagate` as appropriate; no `basicConfig`.
6. Fix all imports across `src/tts_engine/`, `tests/`, `tests-e2e/` (`tts_mcp` → `tts_engine`); rename `test_server.py` → `test_mcp.py`; add `test_tools.py`.
7. Add the `__init__.py` re-exports (`TTSEngine`, `load_config`) with `__all__`.
8. Update `tests/test_project_map.py` (`_PKG`, regex).
9. Update `AGENTS.md` and `README.md` per Scope (not the config-schema JSON).
10. `uv sync` (entry-point/name change) and run the verification gate.

## Verification

- `tests/test_tools.py` covers the three `speak` paths; `tests/test_mcp.py` covers the wrapper; `tests/test_project_map.py` passes against the renamed package and the new `tools.py`/`mcp.py` map rows.
- Gate: `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass.
- On success, promote to **Implemented** (here + [specs/_index.md](../specs/_index.md)): [tools.md](../specs/tools.md), [mcp-server.md](../specs/mcp-server.md), [tts-module-interface.md](../specs/tts-module-interface.md), [elevenlabs-module.md](../specs/elevenlabs-module.md), [audio-player.md](../specs/audio-player.md), [testing.md](../specs/testing.md). [project.md](../specs/project.md) and [architecture.md](../specs/architecture.md) stay **Updated** until the next plan (they still need `from_config`, `TTSEngineConfig` in the public API, and config-driven logging level). Mark this plan `Done` in [_index.md](_index.md).
