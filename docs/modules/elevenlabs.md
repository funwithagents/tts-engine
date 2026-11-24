# `elevenlabs`

**Kind:** `hosted` · **Synthesis:** ElevenLabs cloud API · **Install:** `tts-engine[elevenlabs]` · **Credentials:** API key · **Voice listing:** yes

The ElevenLabs module streams MP3 from the ElevenLabs API and decodes it to signed 16-bit PCM in process before passing it to the audio sink. Every model works for both a complete text and a text stream: a text stream goes over the text-to-speech WebSocket with a v2 model, or over the text-to-dialogue WebSocket with a v3/v4 model such as `eleven_v4_turbo` (see [`examples/config.elevenlabs-v4.json`](../../examples/config.elevenlabs-v4.json)). Install it with the `elevenlabs` extra:

```bash
uv sync --no-dev --extra elevenlabs
# For an installed package:
pip install "tts-engine[elevenlabs]"
```

The API key is read from an environment variable, so no secret appears in the configuration file:

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
| `api_key_env` | no | `ELEVENLABS_API_KEY` | Name of the environment variable holding the API key. The key itself is never written in the config: a config with `api_key` is refused. |
| `voice_id` | yes | — | ElevenLabs voice ID. |
| `model` | no | `eleven_flash_v2_5` | Model ID. `eleven_flash_v2_5` favors latency; `eleven_multilingual_v2` favors quality; `eleven_v4_turbo`, `eleven_v4` and `eleven_v3*` stream over the text-to-dialogue socket, which holds a dialogue session from a separate, smaller pool. |
| `stability` | no | `0.5` | Voice stability from 0.0 to 1.0. |
| `similarity_boost` | no | `0.75` | Similarity boost from 0.0 to 1.0. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails: each read of a `say()` response, the opening of the `say_stream()` socket, and the next message once the whole text has been sent. A cancel issued while a `say()` request is stalled can take that long. |

The module emits mono PCM at 44,100 Hz.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` (`ELEVENLABS_API_KEY` by default) is set in the process running the engine.
- `does not take 'api_key'` means the config still writes the key itself: move it to an environment variable and name that variable in `api_key_env`.
- Verify that `voice_id` exists and is available to the configured ElevenLabs account.

## See also

- [specs/modules/elevenlabs.md](../../specs/modules/elevenlabs.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.elevenlabs.json](../../examples/config.elevenlabs.json): a complete MCP-server config, with the module's defaults.
- [examples/config.elevenlabs-v4.json](../../examples/config.elevenlabs-v4.json): the same with a v4 model (the text-to-dialogue socket).
- [Latency benchmark](../../benchmarks/README.md#results): measured time to first audio for each of its models.
