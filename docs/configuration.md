# Configuring an engine

`TTSEngine` consumes a `TTSEngineConfig`. Whether it comes from a dictionary, JSON text, or a file, its data has the same **engine-block** shape. `module.type` selects the module and every other `module` field is specific to it (this example uses `pocket`; see [TTS modules](modules/index.md)):

```json
{
  "module": {
    "type": "pocket",
    "voice": "alba"
  },
  "player": {
    "device": null
  }
}
```

Pass that object directly to a loader; do not add an outer `"engine"` key. A larger application can store it under any key it chooses, while the optional MCP server format specifically stores it under `"engine"`.

The [`examples/`](../examples) directory holds one complete MCP-server config per module (`config.pocket.json`, `config.phonon.json`, `config.qwen3.json`, `config.kokoro.json`, `config.piper.json`, `config.elevenlabs.json`, `config.elevenlabs-v4.json`, `config.gradium.json`, `config.mistral.json`, `config.deepgram.json`, `config.deepgram-flux.json`, `config.gemini.json`, `config.cartesia.json`, `config.openai.json`, `config.openai-compatible.json`, `config.tone.json`, `config.audiofile.json`). No single example is the default.

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

**Relative file paths.** Module fields that name files (an `audiofile` WAV, a pocket voice `.wav`) resolve against the module's reserved `base_dir` key. `from_json_file()` fills it in with the config file's own directory, so paths are relative to the file rather than the working directory. `from_dict()` and `from_json()` accept a `base_dir=` keyword for the same purpose. Without one, relative paths resolve against the working directory. An explicit `base_dir` in the module block wins, and a relative one is itself resolved against the loader's directory.

`from_dict()` and the JSON loaders validate the shared structure before the selected module validates and resolves its own fields during engine construction. For example, the ElevenLabs module reads the environment variable named by `api_key_env` at that point.

The optional `player.device` field selects the local output device: `null` uses the system default, a string selects by name, and an integer selects by device index. To inspect available devices:

```bash
uv run python -c "import sounddevice; print(sounddevice.query_devices())"
```
