---
code:
  - src/tts_engine/mcp.py
  - src/tts_engine/mcp_server_cli.py
tests:
  - tests/test_mcp.py
  - tests/test_mcp_server_cli.py
  - tests/test_no_mcp_import.py
---

# MCP Server

**Status:** Implemented

## Overview

The MCP server (`mcp.py`) exposes the engine's tools over StreamableHTTP. It is built with the official MCP Python SDK and served by `uvicorn`. It is a **thin** layer: it builds one `TTSTools(engine)` and registers each method behind a small wrapper that delegates to the transport-agnostic tools layer (see [tools.md](tools.md)); the server contains no synthesis or validation logic of its own.

## Installation

The MCP server ships behind the **`mcp` extra** — `pip install tts-engine[mcp]` / `uv sync --extra mcp` — which declares `mcp` (the SDK, without its `cli` extra) and `uvicorn`. Neither is a base dependency: a library caller or an agent embedding `TTSTools` never installs the server stack (see [project.md](project.md), "Dependency strategy for transports"). A deployment combines it with a provider extra, e.g. `tts-engine[mcp,elevenlabs]`; the `tone` fixture module needs no provider extra.

- `mcp.py` imports the SDK at the top of the file; it is only reachable by explicit submodule import, so without the extra `import tts_engine.mcp` raises a plain `ModuleNotFoundError`.
- `mcp_server_cli.py` has no top-level `mcp`/`uvicorn` import, because the `tts-engine-mcp` console script is installed even without the extra. `main` parses arguments, then imports `uvicorn` and `create_server`; if the missing module is `mcp`, `uvicorn`, or one of their submodules, it exits with status 1 and `tts-engine-mcp requires the mcp extra: pip install tts-engine[mcp]` on stderr. Any other import error is re-raised unchanged.
- `MCPServerConfig` lives in `config.py` with no MCP imports, so it stays available (and re-exported from `tts_engine`) without the extra.

## Transport

StreamableHTTP, mounted at `/mcp`. Default bind: `127.0.0.1:8000` (configurable via `server.host` / `server.port`).

MCP clients connect to `http://<host>:<port>/mcp`.

## Tool: `say`

### Input schema

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | yes | The text to synthesize and play. Must be non-empty. |

### Behaviour

The registered `say` tool is a thin wrapper over a `TTSTools` instance:

```python
tools = TTSTools(engine)


@mcp.tool()
async def say(text: str) -> str:
    return await tools.say(text)
```

The empty-text guard, the call to `engine.say`, and the `TTSError` → message mapping all live in `TTSTools.say` (see [tools.md](tools.md)). The wrapper only adapts the MCP call to that method.

### Return value (success)

```json
[{"type": "text", "text": "OK"}]
```

### Return value (error)

Expected synthesis errors (`TTSError`) are returned as MCP text content rather than raised as exceptions, so the client receives a structured error rather than a transport-level failure:

```json
[{"type": "text", "text": "TTS error: <message>"}]
```

Unexpected exceptions, including downstream playback/device failures, are deliberately not caught by the tools or MCP layers and surface as MCP tool-execution failures. This preserves their real identity instead of mislabeling them as provider errors.

## Lifecycle

- `main` runs in this order: parse `--config`/`--log-level` → import the MCP stack (the extra check above) → `MCPServerConfig.from_json_file` → `logging.basicConfig` → `TTSEngine(cfg.engine)` → `create_server(engine)` → `uvicorn.run`.
- The server is created by `create_server(engine)` and started in [`mcp_server_cli.py`](../src/tts_engine/mcp_server_cli.py) via `uvicorn.run`.
- `TTSEngine` is constructed before the server starts (via `TTSEngine(cfg.engine)`) and injected into `create_server` (no lazy init).
- The server does not restart the engine on failure — crash = process exit.

## Logging

Application-level logging uses `log = logging.getLogger(__name__)` (a child of the `tts_engine` package logger). The entry point configures the root logger with `logging.basicConfig(level=..., format=...)`; the level comes from its `--log-level` flag (default `INFO`; one of `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`), not from the config file — see [project.md](project.md), "Logging". uvicorn is started from `mcp_server_cli.py` via `uvicorn.run(...)` with its default log configuration; no custom `log_config` is passed.

## MCP SDK usage pattern

```python
from mcp.server.fastmcp import FastMCP

from tts_engine.tools import TTSTools


def create_server(engine: TTSEngine) -> FastMCP:
    mcp = FastMCP("tts-engine")
    tools = TTSTools(engine)

    @mcp.tool()
    async def say(text: str) -> str:
        return await tools.say(text)

    return mcp
```

The server is run via the SDK's StreamableHTTP transport using `uvicorn`.
