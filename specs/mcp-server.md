---
code:
  - src/tts_mcp/server.py
tests:
  - tests/test_server.py
---

# MCP Server

**Status:** Implemented

## Overview

The MCP server exposes a single tool (`speak`) over StreamableHTTP. It is built with the official MCP Python SDK and served by `uvicorn`.

## Transport

StreamableHTTP, mounted at `/mcp`. Default bind: `127.0.0.1:8000` (configurable via `server.host` / `server.port`).

MCP clients connect to `http://<host>:<port>/mcp`.

## Tool: `speak`

### Input schema

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | yes | The text to synthesize and play. Must be non-empty. |

### Behaviour

1. Validate that `text` is non-empty. Return an error content block if not.
2. Call `await engine.speak(text)`.
3. Return a success content block when playback completes.

### Return value (success)

```json
[{"type": "text", "text": "OK"}]
```

### Return value (error)

Tool errors are returned as MCP error content (not raised as exceptions), so the client receives a structured error rather than a transport-level failure:

```json
[{"type": "text", "text": "TTS error: <message>"}]
```

## Lifecycle

- The server is created and started in `cli.py` via `uvicorn.run`.
- `TTSEngine` is constructed before the server starts and injected into the server (no lazy init).
- The server does not restart the engine on failure — crash = process exit.

## Logging

Application-level logging uses `log = logging.getLogger(__name__)`. uvicorn is started from `cli.py` via `uvicorn.run(...)` with its default log configuration; no custom `log_config` is passed.

## MCP SDK usage pattern

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tts-mcp")

@mcp.tool()
async def speak(text: str) -> str:
    ...
```

The server is run via the SDK's StreamableHTTP transport using `uvicorn`.
