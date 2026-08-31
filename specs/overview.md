---
# Project-wide overview — governs no single module, so `code:` is intentionally omitted.
tests: []
---

# Overview

**Status:** Stable

## Purpose

`tts-engine` is a streaming text-to-speech engine. Its core is a reusable `TTSEngine` that synthesizes a text string through a pluggable cloud TTS provider and plays the audio in real-time on the machine it runs on. The engine is usable two ways:

- **As a library** — import `tts_engine`, build an engine from config, and call `await engine.speak(text)` directly from your own Python code.
- **As an MCP server** — run the `tts-engine-mcp` entry point to expose the engine's tools (the `speak` tool) to any MCP client (an AI agent, Claude Desktop, a test client).

The MCP is one interface onto the engine, not the whole product. The engine, the tools, and the MCP are three distinct layers (see [architecture.md](architecture.md)).

## Goals

- Provide a clean, importable `TTSEngine` built from a single `TTSEngineConfig` (module + player)
- Expose the engine's capabilities as provider-agnostic **tools** (`speak`), reusable independently of any transport
- Ship an **MCP server** that exposes those tools over StreamableHTTP, deployable on a local network
- Stream audio from the provider to the audio device with minimal latency (playback starts before the full audio is received)
- Support pluggable TTS backends, with ElevenLabs as the first implementation
- Keep configuration simple: one config file, one active module, one place for logging level

## Non-goals (v1)

- No file output / audio asset generation (`synthesize` tool)
- No voice listing (`list_voices` tool)
- No MCP resources
- No audio content verification
- No multi-module routing or fallback
- No client-side audio streaming (bytes returned to MCP client)

## Intended use

The engine runs on a machine with speakers.

- **Library use case** — application code constructs a `TTSEngine` and calls `speak()` to read text aloud on that machine, without any MCP transport in the loop.
- **MCP use case** — an AI agent or human using an MCP client sends text to the `speak` tool and hears the result immediately on the server machine.

Example use cases:
- AI assistant reads out a response aloud
- Notification system speaks alerts
- Application embeds the engine directly to voice its own output
- Developer testing TTS output during model/voice tuning

## Public API

Importing the package exposes the engine and its config:

```python
from tts_engine import TTSEngine, TTSEngineConfig, load_config

cfg = load_config("config.json")  # -> AppConfig(engine, server, logging)
engine = TTSEngine.from_config(cfg.engine)  # builds module + player from config
await engine.speak("Hello world")
```

See [architecture.md](architecture.md) for the layer breakdown and [configuration.md](configuration.md) for the config shape.
