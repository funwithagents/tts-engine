---
code:
  - src/tts_engine/engine.py
  - src/tts_engine/__init__.py
tests:
  - tests/test_engine.py
---

# Architecture

**Status:** Implemented

## Layers

The repo is three layers, from reusable core outward to transport:

1. **`TTSEngine`** (`engine.py`) — the reusable core. Holds a `TTSModule` + `AudioPlayer` and exposes `speak(text)`. No protocol knowledge, no transport, no MCP.
2. **Tools** (`tools.py`) — provider- and transport-agnostic functions over an engine, e.g. `speak(engine, text) -> str`. They own input guards (empty text) and turn `TTSError` into a caller-friendly string. Usable directly from library code or from any transport.
3. **MCP** (`mcp.py`) — the MCP server. Registers thin `@mcp.tool()` wrappers that delegate to the tools layer and serves them over StreamableHTTP.

A library user stops at layer 1 or 2; an MCP client goes through layer 3.

## Component overview

```
┌─────────────────────────────────────────────────────┐
│ MCP Client (AI agent, Claude Desktop, test client)  │      Library caller
└──────────────────────┬──────────────────────────────┘      (own Python code)
                       │ StreamableHTTP (speak tool call)             │
┌──────────────────────▼──────────────────────────────┐             │
│ MCP Server  (mcp.py)                                │             │
│  • Registers the speak tool                         │             │
│  • Thin wrapper: delegates to tools.speak           │             │
└──────────────────────┬──────────────────────────────┘             │
                       │                                            │
┌──────────────────────▼──────────────────────────────┐             │
│ Tools  (tools.py)                                   │             │
│  • speak(engine, text) -> str                       │◀────────────┘
│  • empty-text guard, TTSError → message             │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│ TTSEngine  (engine.py)                              │
│  • TTSEngine(module, player)                         │
│  • TTSEngine.from_config(TTSEngineConfig)            │
│  • speak(text) → streams module output              │
│    to AudioPlayer via callback                       │
└────────────┬─────────────────────────┬──────────────┘
             │                         │
┌────────────▼──────────┐  ┌──────────▼──────────────┐
│ TTSModule             │  │ AudioPlayer  (audio.py)  │
│ (modules/base.py ABC) │  │  • Opens sounddevice     │
│                       │  │    output stream         │
│ elevenlabs.py         │  │  • feed(chunk: bytes)    │
│  • Calls ElevenLabs   │  │    writes PCM to device  │
│    streaming API      │  └─────────────────────────-┘
│  • Decodes MP3→PCM    │
│    via miniaudio      │
│  • Calls callback per │
│    PCM chunk          │
└────────────┬──────────┘
             │ HTTPS streaming
┌────────────▼──────────┐
│ ElevenLabs API        │
└───────────────────────┘
```

## `TTSEngine` construction

`TTSEngine` has two constructors:

```python
class TTSEngine:
    def __init__(self, module: TTSModule, player: AudioPlayer) -> None:
        """Low-level injection. Used by tests (fake module/player, no audio hardware)."""

    @classmethod
    def from_config(cls, config: TTSEngineConfig) -> "TTSEngine":
        """Build the module (via load_module) and player (AudioPlayer) from config,
        then construct the engine."""
```

- `from_config` is the entry point for real use (library and MCP). It reads `config.module` (the raw module dict, including `type`) through `load_module`, and `config.player` (a `PlayerConfig`) to build the `AudioPlayer`.
- The plain `__init__(module, player)` keeps dependency injection available so the engine's behavior is tested without touching a TTS provider or audio hardware.

See [configuration.md](configuration.md) for `TTSEngineConfig` / `PlayerConfig`.

## Data flow

### Through the MCP

1. MCP client sends a `speak` tool call with `{"text": "Hello world"}`.
2. `mcp.py`'s `speak` wrapper calls `tools.speak(engine, text)`.
3. `tools.speak` guards empty text, then `await engine.speak(text)`, mapping `TTSError` to an error string.
4. `engine.speak` builds a `TTSOptions()` and calls `module.stream(text, options, callback=player.feed)`.
5. The ElevenLabs module opens an HTTPS streaming connection requesting MP3 (`mp3_44100_128`) and decodes each chunk to raw signed 16-bit PCM mono via `miniaudio.stream_any` in-process.
6. As decoded PCM chunks are produced, the module calls `player.feed(chunk)` for each one.
7. `AudioPlayer.feed` writes the chunk to the open `sounddevice` output stream — playback begins on the first chunk.
8. When the stream ends, `engine.speak` returns; `tools.speak` returns `"OK"`; `mcp.py` returns it to the client.

### From library code

Steps 3–7 above, entered directly: application code calls `tools.speak(engine, text)` (for the guarded, string-returning contract) or `await engine.speak(text)` (raw) — no MCP layer involved.

## Component responsibilities

| Component | Responsibility |
|-----------|---------------|
| `mcp.py` | MCP protocol, tool registration, StreamableHTTP; thin wrappers over `tools`; builds the `FastMCP` app |
| `tools.py` | Provider/transport-agnostic operations over an engine (`speak`); input guards; `TTSError` → string |
| `engine.py` | Wires module + player; `speak()`; `from_config`; no protocol knowledge |
| `modules/base.py` | Defines `TTSModule` ABC and shared dataclasses (`TTSOptions`) |
| `modules/elevenlabs.py` | ElevenLabs API interaction, MP3→PCM decoding via miniaudio, config parsing |
| `audio.py` | `sounddevice` output stream management; format-agnostic PCM consumer |
| `config.py` | Load, parse, and validate `config.json`; produce typed config dataclasses (`AppConfig`, `TTSEngineConfig`, …) |
| `cli.py` | Argument parsing; `load_config` → `TTSEngine.from_config` → MCP server; `setup_logging(level)`; starts uvicorn |
| `__init__.py` | Public API surface: re-exports `TTSEngine`, `TTSEngineConfig`, `load_config` |

## Public API

The package exposes the library entry points at the top level:

```python
from tts_engine import TTSEngine, TTSEngineConfig, load_config
```

Everything else (modules, audio, tools, mcp, cli) is reachable by submodule import but is not part of the curated top-level surface.

## Threading / async model

- The MCP server runs under `uvicorn` (async).
- `engine.speak` is an `async` method; it `await`s the module's streaming coroutine.
- `AudioPlayer` uses a `sounddevice` output stream in blocking-write mode: `feed()` writes each PCM chunk directly to the stream. The module drives the callback from a single `asyncio.to_thread` worker, so `feed()` is only ever called from one thread at a time.
- Only one `speak` call is processed at a time (no concurrent synthesis).
