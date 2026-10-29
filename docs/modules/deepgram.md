# `deepgram`

**Kind:** `hosted` · **Synthesis:** Deepgram cloud API (Aura-2, Flux TTS) · **Install:** `tts-engine[deepgram]` · **Credentials:** API key · **Voice listing:** yes

The Deepgram module streams speech from Deepgram's hosted voices over the official `deepgram-sdk` WebSockets: the Aura-2 voices, or the newer Flux TTS voices when `model` starts with `flux-` (e.g. `flux-haley-en`; see [`examples/config.deepgram-flux.json`](../../examples/config.deepgram-flux.json)). Install it with the `deepgram` extra:

```bash
uv sync --no-dev --extra deepgram
# For an installed package:
pip install "tts-engine[deepgram]"
```

```json
{
  "module": {
    "type": "deepgram",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "aura-2-thalia-en"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `model` | no | `aura-2-thalia-en` | The model, which is also the voice: an Aura model, `aura-2-<voice>-<language>` (e.g. `aura-2-hector-fr`), or a Flux model, `flux-<voice>-<language>` (e.g. `flux-haley-en`). |
| `sample_rate` | no | `24000` | Output rate: `8000`, `16000`, `24000`, `32000` or `48000`. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier: 0.7 to 1.5 with an Aura model; 0.5 to 1.5 in steps of 0.05 with a Flux model. |
| `expressivity` | no | API default | Flux models only: how expressive the delivery is, an integer from -2 to 2. |
| `mip_opt_out` | no | API default | `true` opts out of Deepgram's Model Improvement Program. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. A text stream goes to the socket as it arrives, and a complete text in small slices. Text is handed to the server in flushed segments, one at a time, and only while the audio already received runs less than about ten seconds ahead of playback, so the server never holds more unspoken text than it accepts and a long reply does not pile up audio in memory.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting HTTP 401 means the key was rejected; HTTP 403 usually means the `model` does not exist or the account cannot use it; HTTP 400 means a value such as `speed` is out of range. A Flux model may also reject a particular `speed` for its voice (`SPEED_NOT_SUPPORTED`).

## See also

- [specs/modules/deepgram.md](../../specs/modules/deepgram.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.deepgram.json](../../examples/config.deepgram.json): a complete MCP-server config, with the module's defaults.
- [examples/config.deepgram-flux.json](../../examples/config.deepgram-flux.json): the same with a Flux voice (speak v2).
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
