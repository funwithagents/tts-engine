# `gemini`

**Kind:** `hosted` · **Synthesis:** Google Gemini API (Gemini TTS) · **Install:** `tts-engine[gemini]` · **Credentials:** API key · **Voice listing:** no

The Gemini module streams speech from Google's Gemini TTS models through the official `google-genai` SDK and the Gemini Developer API. Install it with the `gemini` extra:

```bash
uv sync --no-dev --extra gemini
# For an installed package:
pip install "tts-engine[gemini]"
```

```json
{
  "module": {
    "type": "gemini",
    "api_key_env": "GEMINI_API_KEY",
    "voice": "Kore"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | no | `GEMINI_API_KEY` | Name of the environment variable holding the API key. The key itself is never written in the config: a config with `api_key` is refused. |
| `voice` | no | API default | A prebuilt voice name, e.g. `Kore`, `Puck`, `Charon` or `Zephyr`. |
| `model` | no | `gemini-3.8-flash-tts` | Gemini TTS model ID, e.g. `gemini-3.8-flash-lite-tts`. |
| `language_code` | no | detected from the text | BCP-47 language code, e.g. `en-US` or `fr-FR`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at 24,000 Hz. Text is sent verbatim as the prompt, so natural-language direction ("Say cheerfully: …") and inline tags pass through. A text stream is synthesized one request per sentence, which has two limitations:

- Each sentence starts after a short pause, the time to first audio of one request: 691 ms median, 991 ms at p90 for `gemini-3.8-flash-tts` in the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md).
- The Gemini API limits requests per model per minute (10 on Tier 1). A text stream of more than ~10 sentences within a minute fails partway with a `TTSError` reporting `429 RESOURCE_EXHAUSTED`; a higher usage tier raises the limit.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` (`GEMINI_API_KEY` by default) is set in the process running the engine.
- `does not take 'api_key'` means the config still writes the key itself: move it to an environment variable and name that variable in `api_key_env`.
- A `TTSError` containing `No matching speaker voice` means `voice` is not a prebuilt voice; `NOT_FOUND` means the `model` ID is wrong; `Invalid language_code` means `language_code` is not a BCP-47 code the API accepts.
- A `TTSError` containing `429 RESOURCE_EXHAUSTED` means the per-minute request quota of the model is spent (one request per sentence): wait a minute, shorten the text, or move the Google account to a higher tier.
- A `TTSError` reporting that Gemini stopped synthesis early or blocked the prompt carries the API's reason, e.g. `SAFETY`.

## See also

- [specs/modules/gemini.md](../../specs/modules/gemini.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.gemini.json](../../examples/config.gemini.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/README.md#results): measured time to first audio for each of its models.
