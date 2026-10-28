# `cartesia`

**Kind:** `hosted` · **Synthesis:** Cartesia cloud API (Sonic) · **Install:** `tts-engine[cartesia]` · **Credentials:** API key · **Voice listing:** yes

The Cartesia module streams speech from Cartesia's Sonic models through the official `cartesia` SDK and its realtime WebSocket. Install it with the `cartesia` extra:

```bash
uv sync --no-dev --extra cartesia
# For an installed package:
pip install "tts-engine[cartesia]"
```

```json
{
  "module": {
    "type": "cartesia",
    "api_key_env": "CARTESIA_API_KEY",
    "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | Cartesia voice ID (a UUID from the voice library or a cloned voice); the example is "Tessa", an English voice. |
| `model` | no | `sonic-3.6` | Sonic model ID, e.g. `sonic-3`, a dated snapshot, or `sonic-latest`. |
| `sample_rate` | no | `24000` | Output rate: `8000`, `16000`, `22050`, `24000`, `44100`, or `48000`. |
| `language` | no | API default | Language code of the text, e.g. `en` or `fr`. |
| `speed` | no | API default | Speaking-rate multiplier, `0.6`–`1.5`. |
| `volume` | no | API default | Volume multiplier, `0.5`–`2.0`. |
| `emotion` | no | neutral | An emotion name such as `happy`, `calm` or `sympathetic`. |
| `max_buffer_delay_ms` | no | API default (3000) | How long the server may wait for more text before speaking a fragment with no sentence end, `0`–`5000`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. A text stream goes to the socket as it arrives, and a complete text in small slices. Text is handed to the server one segment per Cartesia context, the next context opened once the server has acknowledged the previous one and only while the audio already received runs less than about ten seconds ahead of playback. If the text source pauses for several seconds, the module ends the idle context and continues on a fresh one, so the rest of the text is still spoken.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting HTTP 401 means the key was rejected; `Voice not found` or `Model not found` (status 404) means `voice_id` or `model` is wrong.
- An unknown `emotion` is not rejected by the API: speech is produced without it.

## See also

- [specs/modules/cartesia.md](../../specs/modules/cartesia.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.cartesia.json](../../examples/config.cartesia.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/latency/RESULTS.md): measured time to first audio for each of its models.
