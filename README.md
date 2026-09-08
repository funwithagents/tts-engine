# tts-engine

A streaming text-to-speech engine. Text goes in, audio plays out on the local machine in real time. Use it three ways:

- **As a Python library** — import the engine and `await engine.say(text)`.
- **Inside your own agent** — register `tools.say` directly, no MCP required.
- **As an MCP server** — expose a `say` tool to any MCP-compatible AI client.

---

## How it works

The core is a reusable `TTSEngine`. It synthesizes text through a **pluggable TTS module** — a swappable backend such as ElevenLabs (cloud) or pocket-tts (local) — and streams the audio to the local audio device chunk by chunk, so sound starts playing with minimal latency, before synthesis finishes.

Around that core are two thin layers. The three ways to use the package map onto the three layers, each a thin wrapper over the one below it:

| Layer | How you use it |
|---|---|
| **`TTSEngine`** | call `engine.say(text)` directly — the library |
| **`TTSTools`** | register `tools.say` with your agent framework — the tools |
| **MCP server** | connect an MCP client over the network — the MCP interface |

Which TTS module runs is chosen by config and can be swapped without touching any of these layers — see [TTS modules](#tts-modules). The MCP is one interface onto the engine, not the whole product.

---

## Installation

```bash
git clone <repo-url>
cd tts-engine
uv sync
```

Requirements:

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- **PortAudio**, for audio playback (needed by every backend):
  ```bash
  sudo apt-get install libportaudio2
  ```

The base install includes the framework and the `elevenlabs` backend. Each backend has its own requirements — an API key, or a heavier optional extra — documented with it under [TTS modules](#tts-modules) (e.g. `uv sync --extra pocket` for the local model).

---

## TTS modules

The engine synthesizes through one **module** — a swappable TTS backend. Exactly one is active, selected by `engine.module.type` in your [config](#configuration); the remaining fields under `module` are specific to that backend. The tools layer, playback, and MCP server never change with the module.

### `elevenlabs` — cloud API

Streams audio from the ElevenLabs API and decodes it to PCM on the fly. Part of the base install; needs an [API key](https://elevenlabs.io). Provide it via the environment (never commit it):

```bash
export ELEVENLABS_API_KEY="sk_..."
```

```json
"module": {
  "type": "elevenlabs",
  "api_key_env": "ELEVENLABS_API_KEY",
  "voice_id": "JBFqnCBsd6RMkjVDRZzb",
  "model": "eleven_flash_v2_5",
  "stability": 0.5,
  "similarity_boost": 0.75
}
```

| Field | Required | Description |
|---|---|---|
| `api_key_env` | one of these two | Name of the env var holding your API key (recommended — keeps secrets out of the file) |
| `api_key` | one of these two | Literal API key; supported, but avoid committing it |
| `voice_id` | yes | Voice to use — find IDs in the [ElevenLabs voice library](https://elevenlabs.io/voice-library) |
| `model` | no (`eleven_flash_v2_5`) | Model ID (`eleven_flash_v2_5` for low latency, `eleven_multilingual_v2` for quality) |
| `stability` / `similarity_boost` | no (`0.5` / `0.75`) | Voice tuning parameters (0.0–1.0) |

### `pocket` — local model

Runs [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) in-process — a small (~100M-parameter) model, no API key and no network at synthesis time, ~10× real-time on an M-series CPU. It pulls in `torch` and downloads weights on first use, so it installs behind an extra:

```bash
uv sync --extra pocket   # or: pip install tts-engine[pocket]
```

```json
"module": { "type": "pocket", "voice": "alba" }
```

| Field | Required | Description |
|---|---|---|
| `voice` | no (`alba`) | A preset name (26 of them: `alba`, `giovanni`, `lola`, …), a `.wav` path, or a Hugging Face URL |
| `language` | no (`english`) | `english`, `german`, `italian`, `portuguese`, `spanish`, or `french_24l` (plain `french` is unavailable — use `french_24l`) |
| `device` | no (`auto`) | `auto` (cuda if available, else cpu), `cpu`, `cuda`, or `mps` (experimental, opt-in only) |
| `max_tokens` | no (library default) | Per-text-chunk token cap; raise only for unusually long unbroken text |

See [specs/pocket-module.md](specs/pocket-module.md) for the full contract.

### Add your own module

The interface is intentionally minimal:

1. Create a class that extends `TTSModule` (in [src/tts_engine/modules/base.py](src/tts_engine/modules/base.py)) and implements a single `async stream(text, options, callback)` method — it synthesizes text and calls `callback` with each raw PCM chunk as it arrives.
2. Register it by name in the `REGISTRY` dict in [src/tts_engine/modules/\_\_init\_\_.py](src/tts_engine/modules/__init__.py).
3. Select it with `"engine": { "module": { "type": "<your-name>", ... } }` in your config.

The engine, playback layer, tools, and MCP server need no changes — anything that can produce a stream of PCM audio (a local model, another cloud API, …) plugs in this way.

---

## Configuration

The JSON config file is for the **MCP server** — it's what `tts-engine-mcp --config <file>` reads. It wraps a [module](#tts-modules) with the audio player and the server settings:

```json
{
  "engine": {
    "module": { "type": "elevenlabs", "...": "module-specific — see TTS modules" },
    "player": { "device": null }
  },
  "server": { "host": "127.0.0.1", "port": 8000 }
}
```

- **`engine`** (required) — the active `module` (its fields are documented per backend under [TTS modules](#tts-modules)) plus the audio `player`.
- **`server`** (optional, defaults below) — where the MCP server listens.

Start from the shipped template: `cp config.example.json config.json`.

**`engine.player`**

| Field | Description |
|---|---|
| `player.device` | Audio output device — `null` for the system default, or a device name/index. Applies to every module. |

**`server`** (read only by `tts-engine-mcp`; library and tools callers ignore it)

| Setting | Default | Description |
|---|---|---|
| `server.host` / `server.port` | `127.0.0.1` / `8000` | Where the MCP server listens (`http://<host>:<port>/mcp`) |
| `--log-level` *(CLI flag, not config)* | `INFO` | Server log verbosity |

**Using the library instead?** You don't need this file. Build the engine config in memory with `TTSEngineConfig.from_dict(engine_block)` (just the `engine` part — the per-module field tables above still apply), or reuse an existing file's `engine` block via `load_config(path).engine`. The `server` block is MCP-only.

---

## Usage

### As a library

```python
import asyncio

from tts_engine import TTSEngine, load_config


async def main():
    cfg = load_config("config.json")
    engine = TTSEngine(cfg.engine)
    await engine.say("Hello from the TTS engine")


asyncio.run(main())
```

No MCP client, transport, or server is involved — the engine plays on the machine running the code.

### Inside your own agent (tools)

If you're building an agent *without* MCP, register the tools layer directly — the same object the MCP server wraps. `TTSTools` binds an engine and exposes each operation as a method that returns a plain string (never raises for empty input or provider errors) and carries a docstring written to serve as the tool description. Hand a bound method straight to your framework:

```python
from tts_engine import TTSEngine, TTSTools, load_config

engine = TTSEngine(load_config("config.json").engine)
tools = TTSTools(engine)

# `tools.say` is `say(text) -> str`, ready to register as a tool:
# its __name__, docstring, and signature describe the tool to the model.
result = await tools.say("Hello from my agent")  # -> "OK"
```

Use this layer (rather than `engine.say` directly) for the agent-friendly contract: string results and no exceptions for empty text or synthesis failures. See [specs/tools.md](specs/tools.md) for the full return contract.

### As an MCP server

Run the server on a machine with speakers; your MCP client (Claude Desktop, an agent, etc.) connects over the network. It exposes a single tool — **`say(text)`**, which synthesizes `text`, plays it on the server machine, and returns when playback is complete. Transport is StreamableHTTP.

```bash
uv run tts-engine-mcp --config config.json
uv run tts-engine-mcp --config config.json --log-level DEBUG
```

Point your client at `http://<host>:<port>/mcp`. Example with Claude Desktop — add to your `claude_desktop_config.json`:

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

The `say` tool will then be available in your Claude session.

---

## Troubleshooting

**No audio plays** (any backend)
- Confirm PortAudio is installed: `sudo apt-get install libportaudio2`
- Check `engine.player.device` — set it to `null` for the system default, or list devices with `python -c "import sounddevice; print(sounddevice.query_devices())"`

**`elevenlabs`**
- Verify `ELEVENLABS_API_KEY` (or your configured `api_key_env`) is set and active
- Verify your `voice_id` exists in your ElevenLabs account

**`pocket`**
- Ensure the extra is installed: `uv sync --extra pocket`
- First run downloads model weights — allow network access and disk space (cached under the Hugging Face cache afterward)
- On Apple Silicon, keep `device` at `auto` (or `cpu`); `mps` is experimental and can crash mid-synthesis
