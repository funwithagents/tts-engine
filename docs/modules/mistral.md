# `mistral`

**Kind:** `hosted` · **Synthesis:** Mistral cloud API (Voxtral TTS) · **Install:** `tts-engine[mistral]` · **Credentials:** API key · **Voice listing:** yes

The Mistral module streams speech from Mistral's hosted Voxtral TTS models through the official `mistralai` SDK. Install it with the `mistral` extra:

```bash
uv sync --no-dev --extra mistral
# For an installed package:
pip install "tts-engine[mistral]"
```

```json
{
  "module": {
    "type": "mistral",
    "api_key_env": "MISTRAL_API_KEY",
    "voice_id": "01a14675-dc30-778a-8d01-b5dead5a522a"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | no | `MISTRAL_API_KEY` | Name of the environment variable holding the API key. The key itself is never written in the config: a config with `api_key` is refused. |
| `voice_id` | yes | — | A preset voice's ID or slug (`client.audio.voices.list()` lists them), or a custom voice's ID. The API has no default voice. |
| `model` | no | `voxtral-mini-tts-3-0` | Mistral TTS model ID. |
| `expressiveness` | no | API default | Speaker guidance scale, a finite number of at least 1.0. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |
| `server_url` | no | SDK default | API base URL, for a regional or self-hosted endpoint. |

The module emits mono PCM at 24,000 Hz. Text is sent verbatim, so the model's inline emotion and pace tags pass through. A text stream is synthesized one request per sentence.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` (`MISTRAL_API_KEY` by default) is set in the process running the engine.
- `does not take 'api_key'` means the config still writes the key itself: move it to an environment variable and name that variable in `api_key_env`.
- A `TTSError` containing `invalid_voice` means `voice_id` is not a preset or custom voice visible to the account; one containing `invalid_model` means the `model` ID is wrong.

## See also

- [specs/modules/mistral.md](../../specs/modules/mistral.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.mistral.json](../../examples/config.mistral.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/README.md#results): measured time to first audio for each of its models.
