# tts-engine

A streaming text-to-speech engine. Text goes in, audio plays out on the local machine in real time. Use it **as a Python library** (import the engine and call `speak`) or **as an MCP server** that exposes a `speak` tool to any MCP-compatible AI client.

---

## How it works

The core is a reusable `TTSEngine`: it streams audio from a pluggable TTS provider and feeds it to the local audio device chunk by chunk — sound starts playing with minimal latency, before the full audio is even synthesized. Around it are two thin layers: a set of provider-agnostic **tools** (`speak(engine, text)`) and an **MCP server** that exposes those tools over the network. The MCP is one interface onto the engine, not the whole product.

---

## Use as a library

```python
import asyncio

from tts_engine import TTSEngine, load_config


async def main():
    cfg = load_config("config.json")
    engine = TTSEngine.from_config(cfg.engine)
    await engine.speak("Hello from the TTS engine")


asyncio.run(main())
```

No MCP client, transport, or server is involved — the engine plays on the machine running the code.

---

## MCP interface

Run the server on a machine with speakers; your MCP client (Claude Desktop, an agent, etc.) connects over the network. The server exposes a single tool:

**`speak(text)`** — synthesizes `text` and plays it on the server machine. Returns when playback is complete.

Transport: StreamableHTTP. Clients connect to `http://<host>:<port>/mcp`.

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- PortAudio (for audio playback):
  ```bash
  sudo apt-get install libportaudio2
  ```
- An [ElevenLabs API key](https://elevenlabs.io)

---

## Installation

```bash
git clone <repo-url>
cd tts-engine
uv sync
```

---

## Configuration

Copy the example config and provide your ElevenLabs key through the environment:

```bash
cp config.example.json config.json
export ELEVENLABS_API_KEY="sk_..."
```

```json
{
  "engine": {
    "module": {
      "type": "elevenlabs",
      "api_key_env": "ELEVENLABS_API_KEY",
      "voice_id": "voice_id",
      "model": "eleven_flash_v2_5",
      "stability": 0.5,
      "similarity_boost": 0.75
    },
    "player": {
      "device": null
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "logging": {
    "level": "INFO"
  }
}
```

| Field | Description |
|---|---|
| `engine.module.api_key_env` | Name of the environment variable containing your ElevenLabs API key (recommended) |
| `engine.module.api_key` | Optional literal API key; supported, but avoid committing it |
| `engine.module.voice_id` | Voice to use — find IDs in the [ElevenLabs voice library](https://elevenlabs.io/voice-library) |
| `engine.module.model` | ElevenLabs model ID (e.g. `eleven_flash_v2_5` for low latency) |
| `engine.module.stability` / `engine.module.similarity_boost` | Voice tuning parameters (0.0–1.0) |
| `engine.player.device` | Audio output device — `null` for system default, or a device name/index |
| `server.host` / `server.port` | Where the MCP server listens |
| `logging.level` | Log level for the `tts_engine` logger (`DEBUG`/`INFO`/…) |

`server` and `logging` are optional (they default); a pure library caller can provide just `engine`.

## Running the server

```bash
uv run tts-engine-mcp --config config.json
```

## Connecting an MCP client

Point your client at:

```
http://<host>:<port>/mcp
```

Example with Claude Desktop — add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tts": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

The `speak` tool will then be available in your Claude session.


## Architecture: TTS modules

The engine uses a pluggable module system. The `engine.module.type` field in your config selects which module is active — only one runs at a time.

**Built-in modules:**

- **`elevenlabs`** — streams audio from the ElevenLabs API, decodes it on the fly, and feeds raw PCM to the local audio device. Requires an API key; supports voice and model selection.

**Adding your own module** is straightforward — the interface is intentionally minimal:

1. Create a class that extends `TTSModule` (in [src/tts_engine/modules/base.py](src/tts_engine/modules/base.py)) and implements a single `async stream(text, options, callback)` method. The method synthesizes text and calls `callback` with each raw PCM chunk as it arrives.
2. Register it by name in the `REGISTRY` dict in [src/tts_engine/modules/\_\_init\_\_.py](src/tts_engine/modules/__init__.py).
3. Set `"engine": { "module": { "type": "<your-name>", ... } }` in your config.

The server, playback layer, and MCP tool need no changes. This makes it easy to plug in any TTS backend such as a local open-source model, a different cloud API, or anything that can produce a stream of PCM audio.



## Troubleshooting

**No audio plays**
- Confirm PortAudio is installed: `sudo apt-get install libportaudio2`
- Check `engine.player.device` — set it to `null` to use the system default, or run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list available devices

**ElevenLabs API errors**
- Verify `ELEVENLABS_API_KEY` (or your configured `api_key_env`) is set and active
- Verify your `voice_id` exists in your ElevenLabs account
