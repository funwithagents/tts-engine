# tts-engine

A streaming text-to-speech engine for Python. Choose a TTS module, construct a `TTSEngine`, and call `await engine.say(text)`.

Audio is synthesized and consumed chunk by chunk, minimizing the delay before playback begins. The engine includes an ElevenLabs cloud module and an optional local pocket-tts module. By default, audio plays on the machine running the engine; applications can instead provide their own audio sink to capture or route the PCM stream.

Agent-friendly tools and an MCP server are included as optional interfaces over the same engine.

## Installation

This project requires Python 3.11+ and uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
git clone https://github.com/funwithagents/tts-engine.git
cd tts-engine
uv sync
```

Default local playback uses `sounddevice`, which requires PortAudio. On Ubuntu:

```bash
sudo apt-get install libportaudio2
```

The base installation includes the ElevenLabs module. The local pocket-tts module has heavier dependencies and is installed separately:

```bash
uv sync --extra pocket
```

## Quick start

Set an ElevenLabs API key in the environment:

```bash
export ELEVENLABS_API_KEY="sk_..."
```

Construct an engine configuration and speak:

```python
import asyncio

from tts_engine import TTSEngine, TTSEngineConfig


async def main() -> None:
    config = TTSEngineConfig(
        module={
            "type": "elevenlabs",
            "api_key_env": "ELEVENLABS_API_KEY",
            "voice_id": "JBFqnCBsd6RMkjVDRZzb",
        }
    )

    engine = TTSEngine(config)
    await engine.say("Hello from the TTS engine")


asyncio.run(main())
```

No server or protocol is involved. The text is synthesized through the configured module and streamed to the local audio device.

## How the engine works

The reusable core has three parts:

```text
text → TTSEngine.say() → TTSModule → AudioSink
                                      └─ AudioPlayer by default
```

- `TTSEngine` owns the selected module and audio sink and exposes `say(text)`.
- A `TTSModule` connects the engine to one synthesis backend. Exactly one module is selected when the engine is constructed.
- An `AudioSink` consumes the streaming PCM chunks. The built-in `AudioPlayer` sends them to the local audio device.

The module declares its output sample rate, and the engine configures the default player to match it. Calls to `say()` on the same engine are serialized, and each call returns after the sink has been drained.

## Configuring an engine

`TTSEngine` consumes a `TTSEngineConfig`. Whether it comes from a dictionary, JSON text, or a file, its data has the same **engine-block** shape:

```json
{
  "module": {
    "type": "elevenlabs",
    "api_key_env": "ELEVENLABS_API_KEY",
    "voice_id": "JBFqnCBsd6RMkjVDRZzb"
  },
  "player": {
    "device": null
  }
}
```

Pass that object directly to a loader; do not add an outer `"engine"` key. A larger application can store it under any key it chooses, while the optional MCP server format specifically stores it under `"engine"`.

| Source | Constructor | Expected shape |
|---|---|---|
| Python-owned values | `TTSEngineConfig(module=...)` | Constructor arguments; `player` defaults to the system device |
| Dictionary, including one extracted from a larger config | `TTSEngineConfig.from_dict(...)` | Engine block |
| JSON string | `TTSEngineConfig.from_json(...)` | Engine block |
| Standalone engine JSON file | `TTSEngineConfig.from_json_file(...)` | Engine block |

The two common Python patterns are:

```python
from tts_engine import TTSEngine, TTSEngineConfig

# Values owned by this Python code.
config = TTSEngineConfig(module={"type": "pocket", "voice": "alba"})

# Or a validated engine block embedded in a larger application config.
config = TTSEngineConfig.from_dict(application_config["tts"])

engine = TTSEngine(config)
```

`from_dict()` and the JSON loaders validate the shared structure before the selected module validates and resolves its own fields during engine construction. For example, the ElevenLabs module reads the environment variable named by `api_key_env` at that point.

The optional `player.device` field selects the local output device: `null` uses the system default, a string selects by name, and an integer selects by device index. To inspect available devices:

```bash
uv run python -c "import sounddevice; print(sounddevice.query_devices())"
```

## TTS modules

A module is a swappable synthesis backend. Its `type` selects an entry from the module registry, while every other field in the `module` object is specific to that backend. Changing modules does not change `TTSEngine`, playback, tools, or MCP usage.

| Module | Execution | Installation | Credentials |
|---|---|---|---|
| `elevenlabs` | ElevenLabs cloud API | Base installation | API key |
| `pocket` | Local, in-process model | `tts-engine[pocket]` | None |

### `elevenlabs`

The ElevenLabs module streams MP3 from the ElevenLabs API and decodes it to signed 16-bit PCM in process before passing it to the audio sink. It is included in the base installation.

Prefer referencing an environment variable so secrets do not appear in configuration files:

```json
{
  "module": {
    "type": "elevenlabs",
    "api_key_env": "ELEVENLABS_API_KEY",
    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "model": "eleven_flash_v2_5",
    "stability": 0.5,
    "similarity_boost": 0.75
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | ElevenLabs voice ID. |
| `model` | no | `eleven_flash_v2_5` | Model ID. `eleven_flash_v2_5` favors latency; `eleven_multilingual_v2` favors quality. |
| `stability` | no | `0.5` | Voice stability from 0.0 to 1.0. |
| `similarity_boost` | no | `0.75` | Similarity boost from 0.0 to 1.0. |

The module emits mono PCM at 44,100 Hz.

### `pocket`

The pocket module runs [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) in process. It needs no API key and makes no network request during synthesis after its model weights are available. The first use may download weights into the Hugging Face cache.

Install the optional dependencies:

```bash
uv sync --extra pocket
# For an installed package:
pip install "tts-engine[pocket]"
```

Then select the module:

```json
{
  "module": {
    "type": "pocket",
    "voice": "alba",
    "language": "english",
    "device": "auto"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | `alba` | Preset name, local `.wav` path, or supported Hugging Face URL. |
| `language` | no | Model default (`english`) | `english`, `german`, `italian`, `portuguese`, `spanish`, or `french_24l`. |
| `device` | no | `auto` | `auto`, `cpu`, `cuda`, or experimental `mps`. `auto` selects CUDA when available and otherwise CPU. |
| `max_tokens` | no | pocket-tts default | Per-text-chunk token cap. Increase only for unusually long unbroken text. |

The module emits mono PCM at the model's native sample rate, typically 24,000 Hz. On Apple Silicon, `auto` intentionally uses CPU; `mps` remains explicit and experimental.

### Add a module

To add another synthesis backend:

1. Create a class extending `TTSModule` from [`src/tts_engine/modules/base.py`](src/tts_engine/modules/base.py).
2. Implement `sample_rate` and `async stream(text, options, callback)`. The callback receives signed 16-bit little-endian mono PCM chunks.
3. Register the class under a type name in [`src/tts_engine/modules/__init__.py`](src/tts_engine/modules/__init__.py).
4. Select it with `{"module": {"type": "your-module", ...}}` in the engine configuration.

The engine and its integrations require no backend-specific changes.

## Audio output and custom sinks

Without a custom sink, `TTSEngine` constructs an `AudioPlayer` using `player.device` and the active module's sample rate. Playback starts lazily when the first PCM chunk arrives.

Applications that need to capture audio, send it across a network, or feed another playback system can inject an `AudioSink`-compatible object:

```python
class CaptureSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def drain(self) -> None:
        pass


sink = CaptureSink()
engine = TTSEngine(config, sink=sink)
await engine.say("Capture this instead of playing it")
```

The sink receives signed 16-bit little-endian mono PCM at `engine.sample_rate`. `feed()` calls are sequential but may happen off the event-loop thread. `drain()` is called once after every utterance, including when synthesis fails or is cancelled. When a custom sink is supplied, the engine does not construct or use the local audio player.

## Optional integrations

The tools and MCP layers adapt the engine for agents and remote clients. They do not implement synthesis themselves.

```text
MCP client → MCP server → TTSTools → TTSEngine → TTSModule → AudioSink
                         ↑
                non-MCP agent
```

### Use `TTSTools` in an agent

`TTSTools` binds an engine and exposes `say(text) -> str` as an agent-friendly method. It returns `"OK"` on success and converts empty input or expected synthesis failures into a `"TTS error: ..."` result.

```python
from tts_engine import TTSEngine, TTSEngineConfig, TTSTools

config = TTSEngineConfig.from_json_file("tts.json")
engine = TTSEngine(config)
tools = TTSTools(engine)

# Register this bound method directly with an agent framework.
say_tool = tools.say

result = await say_tool("Hello from my agent")
```

Call `engine.say()` directly when you want the raw exception-raising engine contract instead.

### Run the MCP server

The MCP server exposes `TTSTools.say` over StreamableHTTP. Its configuration wraps the same engine block documented above with an optional `server` block:

```json
{
  "engine": {
    "module": {
      "type": "elevenlabs",
      "api_key_env": "ELEVENLABS_API_KEY",
      "voice_id": "JBFqnCBsd6RMkjVDRZzb"
    },
    "player": {
      "device": null
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  }
}
```

Start from [`config.example.json`](config.example.json), then run:

```bash
cp config.example.json config.json
uv run tts-engine-mcp --config config.json
```

The `server` block defaults to `127.0.0.1:8000` when omitted. Connect an MCP client to `http://127.0.0.1:8000/mcp`. Server log verbosity is controlled by the `--log-level` command-line flag rather than the JSON configuration:

```bash
uv run tts-engine-mcp --config config.json --log-level DEBUG
```

Library code can also reuse the engine block from an MCP server configuration:

```python
from tts_engine import MCPServerConfig, TTSEngine

mcp_config = MCPServerConfig.from_json_file("config.json")
engine = TTSEngine(mcp_config.engine)
```

## Troubleshooting

### No audio plays

- Confirm that PortAudio is installed.
- Leave `player.device` as `null` to use the system default, or inspect the available devices with `sounddevice.query_devices()`.
- Remember that audio plays on the machine running `TTSEngine`, not necessarily the machine that initiated an MCP call.

### ElevenLabs

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- Verify that `voice_id` exists and is available to the configured ElevenLabs account.

### pocket-tts

- Ensure the `pocket` extra is installed.
- Allow network access and sufficient disk space when model weights are downloaded for the first time.
- On Apple Silicon, keep `device` set to `auto` or `cpu` unless explicitly testing the experimental `mps` path.

## Development

Install development dependencies and run all default checks:

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The default test run is fast and does not use the network. Live tests against real providers and audio hardware are under `tests-e2e/` and must be invoked explicitly.
