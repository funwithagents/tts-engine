---
code:
  - src/tts_mcp/engine.py
tests:
  - tests/test_engine.py
---

# Architecture

**Status:** Implemented

## Component overview

```
┌─────────────────────────────────────────────────────┐
│ MCP Client (AI agent, Claude Desktop, test client)  │
└──────────────────────┬──────────────────────────────┘
                       │ StreamableHTTP  (speak tool call)
┌──────────────────────▼──────────────────────────────┐
│ MCP Server  (server.py)                             │
│  • Registers the speak tool                         │
│  • Validates input                                  │
│  • Delegates to TTSEngine                           │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│ TTSEngine  (engine.py)                              │
│  • Holds a TTSModule + AudioPlayer                  │
│  • speak(text) → streams module output              │
│    to AudioPlayer via callback                      │
└────────────┬─────────────────────────┬──────────────┘
             │                         │
┌────────────▼──────────┐  ┌──────────▼──────────────┐
│ TTSModule             │  │ AudioPlayer  (audio.py)  │
│ (modules/base.py ABC) │  │  • Opens sounddevice     │
│                       │  │    output stream         │
│ elevenlabs.py      │  │  • feed(chunk: bytes)    │
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

## Data flow

1. MCP client sends a `speak` tool call with `{"text": "Hello world", "voice_id": "..."}`.
2. `server.py` receives the call, extracts `text`, calls `engine.speak(text)`.
3. `engine.speak` builds a `TTSOptions()` and calls `module.stream(text, options, callback=player.feed)`.
4. The ElevenLabs module opens an HTTPS streaming connection to the ElevenLabs API requesting MP3 output (`mp3_44100_128`); it decodes each chunk to raw signed 16-bit PCM mono via `miniaudio.stream_any` in-process.
5. As decoded PCM chunks are produced, the module calls `player.feed(chunk)` for each one.
6. `AudioPlayer.feed` writes the chunk to the open `sounddevice` output stream — playback begins on the first chunk.
7. When the stream ends, `engine.speak` returns; `server.py` returns a success response to the MCP client.

## Component responsibilities

| Component | Responsibility |
|-----------|---------------|
| `server.py` | MCP protocol, tool registration, input validation; builds the `FastMCP` app |
| `engine.py` | Wires module + player; single `speak()` entry point; no protocol knowledge |
| `modules/base.py` | Defines `TTSModule` ABC and shared dataclasses (`TTSOptions`) |
| `modules/elevenlabs.py` | ElevenLabs API interaction, MP3→PCM decoding via miniaudio, config parsing |
| `audio.py` | `sounddevice` output stream management; format-agnostic PCM consumer |
| `config.py` | Load, parse, and validate `config.json`; produce typed config dataclasses |
| `cli.py` | Argument parsing; wires config → engine → server; starts/stops uvicorn via `uvicorn.run` |

## Threading / async model

- The MCP server runs under `uvicorn` (async).
- `engine.speak` is an `async` method; it `await`s the module's streaming coroutine.
- `AudioPlayer` uses a `sounddevice` output stream in blocking-write mode: `feed()` writes each PCM chunk directly to the stream. The module drives the callback from a single `asyncio.to_thread` worker, so `feed()` is only ever called from one thread at a time.
- Only one `speak` call is processed at a time (no concurrent synthesis).
