---
code:
  - src/tts_engine/tools.py
tests:
  - tests/test_tools.py
---

# Tools

**Status:** Implemented

## Purpose

The tools layer sits between the reusable `TTSEngine` and any transport (the MCP, a CLI, application code). It is a single class, `TTSTools`, constructed with an engine; each tool is a provider-agnostic **method** that takes only its own inputs, performs the operation, and returns a caller-friendly result. It owns the concerns that are *about the operation* rather than *about the transport*: input guards and turning engine errors into a stable return contract.

This keeps the MCP layer thin (see [mcp-server.md](mcp-server.md)) and lets the same operations be called directly from library code without an MCP client in the loop.

### Consumers

Three kinds of caller use this layer, and its shape is chosen to serve all three from one implementation:

1. **The MCP server** builds one `TTSTools(engine)` and wraps each method in a thin `@mcp.tool()` (see [mcp-server.md](mcp-server.md)).
2. **A non-MCP agent** registers a **bound method** *directly* as one of its callable tools — no MCP in the loop. Because the engine is held on the instance, `TTSTools(engine).speak` is already an agent-facing `speak(text) -> str`: no argument binding is needed, and the bound method keeps its `__name__`, signature, and docstring — so a framework that builds a tool schema by introspection gets the right name and description. (This is why a class beats a free function here: a `functools.partial` over a free function loses `__name__` and reports `partial`'s own docstring, not the tool's.)
3. **Plain library code** calls a method inline when it wants the guarded, non-raising contract instead of raw `engine.speak`.

Because of consumer (2), two properties are contract, not incidental: the **string return value** (agents receive a result string, never an exception, for expected conditions) and the **self-contained docstring** (it is the tool description — keep it accurate and model-readable when editing a method).

`TTSTools` is part of the curated public API (`from tts_engine import TTSTools`); see [architecture.md](architecture.md), "Public API".

## Core concepts / Decided

- Tools live in `src/tts_engine/tools.py` as **methods of `TTSTools`**, an engine-bound class.
- `TTSTools(engine)` holds the engine on the instance; each method takes only its own inputs and returns a value the caller can use as-is. The engine is an injected dependency (constructor argument), not a module global.
- Tools are **transport-agnostic**: no MCP, FastMCP, or HTTP types appear here. The MCP layer imports and wraps them; it does not reimplement their logic.

### `speak`

```python
class TTSTools:
    def __init__(self, engine: TTSEngine) -> None:
        self._engine = engine

    async def speak(self, text: str) -> str:
        """Synthesize `text` and play it on the machine running the engine.

        - Returns "OK" on success.
        - Returns "TTS error: <message>" if text is empty or synthesis fails.
        """
```

Behavior:

1. If `text` is empty, return `"TTS error: text must not be empty"` without calling the engine.
2. Otherwise `await self._engine.speak(text)`.
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
