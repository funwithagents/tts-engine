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

1. **`TTSEngine`** (`engine.py`) — the reusable core. Built from a `TTSEngineConfig`, it holds a `TTSModule` + an `AudioSink` (the local `AudioPlayer` by default, or an injected custom sink) and exposes `speak(text)`. No protocol knowledge, no transport, no MCP.
2. **Tools** (`tools.py`) — `TTSTools`, an engine-bound class whose methods are provider- and transport-agnostic operations, e.g. `speak(text) -> str`. They own input guards (empty text) and turn `TTSError` into a caller-friendly string. Three kinds of caller use them: the MCP wrappers (layer 3), a **non-MCP agent that registers a bound method directly** (`TTSTools(engine).speak` is already `speak(text) -> str`, keeping its name/docstring for the tool schema), and plain library code wanting the guarded contract. See [tools.md](tools.md), "Consumers".
3. **MCP** (`mcp.py`) — the MCP server. Registers thin `@mcp.tool()` wrappers that delegate to the tools layer and serves them over StreamableHTTP.

A library user stops at layer 1 or 2; an agent embeds layer 2 directly; an MCP client goes through layer 3.

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
│  • TTSTools(engine).speak(text) -> str              │◀────────────┘
│  • empty-text guard, TTSError → message             │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│ TTSEngine  (engine.py)                              │
│  • TTSEngine(TTSEngineConfig)                        │
│    → builds module (load_module) + player (Audio-    │
│      Player) from config                             │
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

`TTSEngine` has a single constructor that takes the engine config, plus an optional audio sink:

```python
class TTSEngine:
    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        """Build the module (via load_module) from config. Use the injected sink,
        or build the default local AudioPlayer from config when sink is None."""
```

- The constructor is the entry point for all use (library and MCP). It reads `config.module` (the raw module dict, including `type`) through `load_module`. If no `sink` is given it builds the default `AudioPlayer` from `config.player` (a `PlayerConfig`) **and the module's declared `sample_rate`** — `AudioPlayer(device=config.player.device, sample_rate=module.sample_rate)`. The module owns the rate (see [tts-module-interface.md](tts-module-interface.md)); the player opens its output stream to match.
- **Sink injection** ([audio-sink.md](audio-sink.md)) makes the playback destination swappable: pass `sink=` to send synthesized PCM somewhere other than the local speaker (a robot pipeline, a buffer, a network stream). When a sink is injected the engine builds no `AudioPlayer` and never imports sounddevice, so the engine stays usable on hosts with no audio device. Backwards compatible — omitting `sink` reproduces today's local playback exactly.
- `TTSEngine.sample_rate` (read-only property) exposes the active module's declared rate, so a custom sink can resample the int16-mono PCM to its destination's format.
- Tests exercise `speak()` behavior without touching a TTS provider or audio hardware by patching `load_module` and `AudioPlayer` in `engine.py` so the constructor yields fakes — dependency injection at the module-boundary rather than the constructor signature — or by injecting an in-memory fake sink directly.

See [configuration.md](configuration.md) for `TTSEngineConfig` / `PlayerConfig`.

## Data flow

### Through the MCP

1. MCP client sends a `speak` tool call with `{"text": "Hello world"}`.
2. `mcp.py`'s `speak` wrapper calls `tools.speak(text)` on its `TTSTools(engine)`.
3. `TTSTools.speak` guards empty text, then `await engine.speak(text)`, mapping `TTSError` to an error string.
4. `engine.speak` builds a `TTSOptions()` and calls `module.stream(text, options, callback=sink.feed)` — the sink being the injected one or the default `AudioPlayer`.
5. The ElevenLabs module opens an HTTPS streaming connection requesting MP3 (`mp3_44100_128`) and decodes each chunk to raw signed 16-bit PCM mono via `miniaudio.stream_any` in-process.
6. As decoded PCM chunks are produced, the module calls `player.feed(chunk)` for each one.
7. `AudioPlayer.feed` writes the chunk to the open `sounddevice` output stream — playback begins on the first chunk.
8. When the stream ends, `engine.speak` returns; `TTSTools.speak` returns `"OK"`; `mcp.py` returns it to the client.

### From library code

Steps 3–7 above, entered directly: application code calls `TTSTools(engine).speak(text)` (for the guarded, string-returning contract) or `await engine.speak(text)` (raw) — no MCP layer involved.

## Component responsibilities

| Component | Responsibility |
|-----------|---------------|
| `mcp.py` | MCP protocol, tool registration, StreamableHTTP; thin wrappers over `tools`; builds the `FastMCP` app |
| `tools.py` | `TTSTools`: engine-bound, provider/transport-agnostic operations (`speak`); input guards; `TTSError` → string |
| `engine.py` | Builds the module from `TTSEngineConfig`; uses an injected `AudioSink` or builds the default `AudioPlayer` (at the module's `sample_rate`); `speak()`; `sample_rate` property; no protocol knowledge |
| `modules/base.py` | Defines `TTSModule` ABC and shared dataclasses (`TTSOptions`) |
| `modules/elevenlabs.py` | ElevenLabs API interaction, MP3→PCM decoding via miniaudio, config parsing |
| `audio.py` | Defines the `AudioSink` Protocol; `AudioPlayer` (its default impl): `sounddevice` output stream management, lazily imported; provider-agnostic consumer of the fixed PCM format contract |
| `config.py` | Load, parse, and validate `config.json`; produce typed config dataclasses (`AppConfig`, `TTSEngineConfig`, …) |
| `mcp_server_cli.py` | Argument parsing (`--config`, `--log-level`); `load_config` → `TTSEngine(cfg.engine)` → MCP server; `logging.basicConfig(level=args.log_level)`; starts uvicorn |
| `__init__.py` | Public API surface: re-exports `TTSEngine`, `TTSEngineConfig`, `TTSTools`, `AudioSink`, `load_config` |

## Public API

The package exposes the library entry points at the top level:

```python
from tts_engine import TTSEngine, TTSEngineConfig, TTSTools, AudioSink, load_config
```

`TTSTools` is curated so agents can register its bound methods directly (see [tools.md](tools.md), "Consumers"). `AudioSink` is exported so embedders can type their own playback destination against the seam ([audio-sink.md](audio-sink.md)). Everything else (modules, the concrete `AudioPlayer`, mcp, mcp_server_cli) is reachable by submodule import but is not part of the curated top-level surface.

## Threading / async model

- The MCP server runs under `uvicorn` (async).
- `engine.speak` is an `async` method; it `await`s the module's streaming coroutine.
- `AudioPlayer` uses a `sounddevice` output stream in blocking-write mode: `feed()` writes each PCM chunk directly to the stream. The module drives the callback from a single `asyncio.to_thread` worker, so `feed()` is only ever called from one thread at a time.
- `TTSEngine` owns an `asyncio.Lock` around the complete module-stream + sink-drain lifecycle. Concurrent library or MCP calls wait for exclusive access, so only one `speak` call is processed at a time and PCM streams cannot interleave — a sink never sees two overlapping utterances.
- A module must stop invoking its callback before `stream()` returns or raises, including cancellation. The ElevenLabs implementation requests cooperative worker shutdown and waits for its thread before propagating cancellation, so the engine can safely drain and release the lock.
