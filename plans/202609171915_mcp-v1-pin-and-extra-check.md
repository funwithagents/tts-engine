# Pin `mcp<2` and narrow the missing-extra check

**Status:** Done

Implements the settled behavior in `specs/project.md` ("Key dependencies", "Dependency strategy for transports") and `specs/mcp-server.md` ("Installation"). Pins the MCP SDK below 2 (a fresh `pip install tts-engine[mcp]` resolves mcp 2.x, which removed `mcp.server.fastmcp`; only `uv.lock` hid it) and makes `tts-engine-mcp` show the install hint only when `mcp`/`uvicorn` itself is missing, so a version mismatch is no longer reported as a missing extra. It deliberately leaves out migrating to the mcp 2.x API.

## Scope

- `pyproject.toml` — `mcp` → `mcp<2` in the `mcp` extra and the `dev` group.
- `uv.lock` — regenerated (stays on mcp 1.26.0).
- `src/tts_engine/mcp_server_cli.py` — exact `exc.name in _MCP_EXTRA_MODULES` instead of the root-package match.
- `tests/test_mcp_server_cli.py` — a missing submodule of the stack (`mcp.server.fastmcp`) and a broken `tts_engine.mcp` both propagate as `ModuleNotFoundError`, not `SystemExit`.
- Specs: `project.md` + `mcp-server.md` `Updated` → `Implemented` (here and in `specs/_index.md`).

## Steps

1. **Packaging.** Pin as scoped; `uv sync`; confirm the lock still has mcp 1.26.0.
2. **CLI.** Replace `(exc.name or "").split(".")[0] not in _MCP_EXTRA_MODULES` with `exc.name not in _MCP_EXTRA_MODULES`; update the comment.
3. **Test.** Parametrize over `"tts_engine.mcp"` and `"mcp.server.fastmcp"`: inside `mocker.patch.dict(sys.modules)`, drop `tts_engine.mcp`, poison the parametrized name with `None`, run `main()` with a valid config, assert `ModuleNotFoundError` whose `name` is the poisoned module.

## Verification

- `uv run pytest tests/test_mcp_server_cli.py tests/test_no_mcp_import.py tests/test_project_map.py`
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`
- Manual: `uv run --isolated --no-project --with '.[mcp]' tts-engine-mcp --config missing.json` gets past the import (config-not-found error, not the install hint).

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
