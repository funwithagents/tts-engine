---
code:
  - src/tts_engine/mcp.py
tests:
  - tests/test_mcp.py
---

# MCP Server

**Status:** Implemented

## Overview

The MCP server (`mcp.py`) exposes the engine's tools over StreamableHTTP. It is built with the official MCP Python SDK and served by `uvicorn`. It is a **thin** layer: each registered tool is a small wrapper that delegates to the transport-agnostic tools layer (see [tools.md](tools.md)); the server contains no synthesis or validation logic of its own.

## Transport

StreamableHTTP, mounted at `/mcp`. Default bind: `127.0.0.1:8000` (configurable via `server.host` / `server.port`).

MCP clients connect to `http://<host>:<port>/mcp`.

## Tool: `speak`

### Input schema

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | yes | The text to synthesize and play. Must be non-empty. |

### Behaviour

The registered `speak` tool is a thin wrapper:

```python
@mcp.tool()
async def speak(text: str) -> str:
    return await tools.speak(engine, text)
```

The empty-text guard, the call to `engine.speak`, and the `TTSError` → message mapping all live in `tools.speak` (see [tools.md](tools.md)). The wrapper only adapts the MCP call to that function.

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

- The server is created by `create_server(engine)` and started in `cli.py` via `uvicorn.run`.
- `TTSEngine` is constructed before the server starts (via `TTSEngine(cfg.engine)`) and injected into `create_server` (no lazy init).
- The server does not restart the engine on failure — crash = process exit.

## Logging

Application-level logging uses `log = logging.getLogger(__name__)` (a child of the `tts_engine` package logger configured by `setup_logging`). uvicorn is started from `cli.py` via `uvicorn.run(...)` with its default log configuration; no custom `log_config` is passed.

## MCP SDK usage pattern

```python
from mcp.server.fastmcp import FastMCP

from tts_engine import tools


def create_server(engine: TTSEngine) -> FastMCP:
    mcp = FastMCP("tts-engine")

    @mcp.tool()
    async def speak(text: str) -> str:
        return await tools.speak(engine, text)

    return mcp
```

The server is run via the SDK's StreamableHTTP transport using `uvicorn`.
