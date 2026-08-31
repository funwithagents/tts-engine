---
code:
  - src/tts_engine/tools.py
tests:
  - tests/test_tools.py
---

# Tools

**Status:** Implemented

## Purpose

The tools layer sits between the reusable `TTSEngine` and any transport (the MCP, a CLI, application code). Each tool is a plain, provider-agnostic function that takes an engine plus its inputs, performs the operation, and returns a caller-friendly result. It owns the concerns that are *about the operation* rather than *about the transport*: input guards and turning engine errors into a stable return contract.

This keeps the MCP layer thin (see [mcp-server.md](mcp-server.md)) and lets the same operations be called directly from library code without an MCP client in the loop.

## Core concepts / Decided

- Tools live in `src/tts_engine/tools.py` as module-level `async` functions.
- A tool takes the `TTSEngine` as its first argument (dependency, not global) and returns a value the caller can use as-is.
- Tools are **transport-agnostic**: no MCP, FastMCP, or HTTP types appear here. The MCP layer imports and wraps them; it does not reimplement their logic.

### `speak`

```python
async def speak(engine: TTSEngine, text: str) -> str:
    """Synthesize `text` and play it via the engine.

    - Returns "OK" on success.
    - Returns "TTS error: <message>" if text is empty or synthesis fails.
    """
```

Behavior:

1. If `text` is empty, return `"TTS error: text must not be empty"` without calling the engine.
2. Otherwise `await engine.speak(text)`.
3. On success, return `"OK"`.
4. On `TTSError`, log it and return `"TTS error: <message>"`.

The empty-text guard and the `TTSError` → string mapping live here (moved out of the MCP server), so any caller — MCP wrapper or library code — gets the same guarded, non-raising contract. Callers that want the raw, exception-raising behavior can call `engine.speak(text)` directly instead.

## Return contract

Tools return strings suitable for direct display to a caller/agent:

| Outcome | Return value |
|---------|--------------|
| Success | `"OK"` |
| Empty input | `"TTS error: text must not be empty"` |
| Synthesis failure (`TTSError`) | `"TTS error: <message>"` |

Tools do not raise for expected error conditions (empty input, provider failure); they encode them in the return value. Unexpected exceptions (programming errors) are not caught.

## Open questions

None currently. Additional tools (e.g. a future `synthesize` for file output) would follow the same shape, but none are in v1 scope (see [overview.md](overview.md), "Non-goals").
