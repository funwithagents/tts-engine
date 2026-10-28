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
  },
  "gain_db": 0
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

## Output gain

Every provider, model and voice speaks at its own level: across ten voices of ten backends, the loudest is more than 8 dB above the quietest. An application that plays several engines through one output (a demo comparing voices, one voice per character, a voice the user picks from a list) hears that as a volume jump at every change. The optional `gain_db` field of the engine block sets one static gain for the engine, in decibels:

```json
{
  "module": { "type": "piper", "voice": "en_US-ryan-medium" },
  "gain_db": -7.6
}
```

- **Negative attenuates, positive amplifies.** The default, `0`, leaves the audio exactly as the module produced it. The accepted range is −60 to +24.
- **It applies to everything the engine plays:** `say` and `say_stream`, the local device and a custom sink, the agent tools and the MCP server. It adds no latency.
- **A positive gain can clip** the loudest peaks. The engine then logs one warning per utterance with the number of clipped samples; lower `gain_db` until it stops.
- **It is per engine, so per voice.** The level depends on the module's whole configuration (voice, model, speed or emotion settings), so give each engine configuration its own value.

**Finding the value.** The [voice audition tool](../tools/voice-audition/README.md) measures each candidate's level when it renders it and shows, on its page, the `gain_db` that brings that voice to a target level (−22 dBFS by default, editable on the page) and the peak once it is applied. Copy that number into the engine block next to the voice's `module`. A target below most voices keeps every gain negative, so nothing clips.
