# Rename the `speak` operation to `say`

**Status:** Done

Implements the renamed operation across every layer: the engine method, the tools method, and the MCP tool are all `say` rather than `speak`. A pure, behavior-preserving rename — no signatures, return contracts, concurrency, or transport change. Historical plans are deliberately left untouched as build records.

## Scope

The files this plan touches, each with a one-line note on what changes:

- `src/tts_engine/engine.py` — `TTSEngine.speak` → `say`; internal `_speak_lock` → `_say_lock`; module docstring
- `src/tts_engine/tools.py` — `TTSTools.speak` → `say`; docstrings referencing `engine.speak` / bound method
- `src/tts_engine/mcp.py` — the registered `@mcp.tool()` `speak` → `say`; module docstring
- `src/tts_engine/mcp_server_cli.py` — argparse description mentioning the `speak` tool
- `tests/test_engine.py`, `tests/test_tools.py`, `tests/test_mcp.py`, `tests/test_no_audio_import.py` — `.say` calls, tool name `"say"`, patch targets, and `test_*`/helper identifier names
- `tests-e2e/test_engine.py`, `tests-e2e/test_mcp.py` — `.say` calls and the `"say"` tool call
- `specs/_index.md`, `specs/overview.md`, `specs/architecture.md`, `specs/tools.md`, `specs/mcp-server.md`, `specs/audio-sink.md`, `specs/testing.md` — prose, embedded code samples, section headers, and the ASCII diagrams (realigned for the shorter word)
- `README.md`, `AGENTS.md` — usage examples, the design-decisions/project-map/data-flow references (CLAUDE.md picks this up via its `@AGENTS.md` include)

Deliberately **out of scope**: the dated `plans/` files (historical records of what was built at the time) and the untracked handoff note. The English words `speak`/`speaks`/`speaker(s)`/`speaking` are preserved wherever they are prose, not the API name.

## Steps

1. Rename the operation token `speak` → `say` across `src/`, `tests/`, `tests-e2e/`, `specs/*.md`, `README.md`, `AGENTS.md`, using word-boundary matching so `speaker`/`speaks`/`speaking` are untouched; also rename `_speak_lock` → `_say_lock`.
2. Restore the one English verb the boundary pass caught: "so the model can speak" in [overview.md](../specs/overview.md).
3. Rename the remaining `test_speak_*` / `_call_speak` identifiers (underscore-joined, so not word-bounded) with a lookahead that spares `speaking`.
4. Realign the ASCII box/data-flow diagrams in [architecture.md](../specs/architecture.md) and [AGENTS.md](../AGENTS.md) whose borders shifted because `say` is two characters shorter than `speak`.
5. The governing specs ([tools.md](../specs/tools.md), [mcp-server.md](../specs/mcp-server.md), [architecture.md](../specs/architecture.md)) stay `Implemented` — spec and code move together in this change, so the code still matches; no status flip is required.

## Verification

- `uv run ruff check .` — clean
- `uv run pyright` — 0 errors
- `uv run pytest` — full unit tier green (includes `test_project_map.py`, the spec-drift guard)
- Repo-wide sweep confirms no API-style `speak` remains in `src/`, `tests/`, `tests-e2e/`, `specs/`, `README.md`, or `AGENTS.md` — only the intended English usages.
