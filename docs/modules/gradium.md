# `gradium`

**Kind:** `hosted` · **Synthesis:** Gradium cloud API · **Install:** `tts-engine[gradium]` · **Credentials:** API key · **Voice listing:** yes

The Gradium module streams raw PCM from the [Gradium](https://gradium.ai) API over a WebSocket, so no decoding happens in process. Install it with the `gradium` extra:

```bash
uv sync --no-dev --extra gradium
# For an installed package:
pip install "tts-engine[gradium]"
```

Prefer referencing an environment variable so secrets do not appear in configuration files:

```json
{
  "module": {
    "type": "gradium",
    "api_key_env": "GRADIUM_API_KEY",
    "voice_id": "91EdXxJDbWICDBgz",
    "model": "default",
    "sample_rate": 48000
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | Gradium voice ID, from the flagship catalog or a custom voice. |
| `model` | no | `default` | Model alias: `default` or `gradium-tts-beta`. |
| `sample_rate` | no | `48000` | Output rate: `8000`, `16000`, `24000`, `44100`, or `48000`. `48000` is native; other rates are resampled by the API. |
| `temp` | no | API default (0.7) | Sampling temperature from 0.0 to 1.4. |
| `cfg_coef` | no | API default (2.0) | Voice similarity from 1.0 to 4.0. |
| `padding_bonus` | no | API default (0.0) | Speech speed from -4.0 to 4.0; negative is faster. |
| `rewrite_rules` | no | the voice's language | Text-rewriting rules applied before synthesis; `none` disables them. |
| `pronunciation_id` | no | — | Pronunciation dictionary ID. |
| `base_url` | no | SDK default | API base URL, for a regional or self-hosted endpoint. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. Voice-setting fields are sent only when set, so the API's own defaults apply otherwise.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- Verify that `voice_id` exists and is available to the configured Gradium account.
- A `TTSError` reporting a sample-rate mismatch means the API answered at a rate other than the configured `sample_rate`; check the value against the supported list.

## See also

- [specs/modules/gradium.md](../../specs/modules/gradium.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.gradium.json](../../examples/config.gradium.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/latency/RESULTS.md): measured time to first audio for each of its models.
