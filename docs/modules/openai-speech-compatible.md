# `openai-speech-compatible`

**Kind:** `hosted` · **Synthesis:** OpenAI's `/v1/audio/speech` (`gpt-4o-mini-tts`, `tts-1`) or an OpenAI-compatible server · **Install:** `tts-engine[openai-compatible]` · **Credentials:** API key · **Voice listing:** no

The `openai-speech-compatible` module streams speech from a `/v1/audio/speech` endpoint through the official `openai` SDK: OpenAI's own by default, or, with `base_url`, any OpenAI-compatible speech server that returns raw `pcm` (vLLM-Omni, Kokoro-FastAPI, `mlx_audio.server`, …). It is named after the API it implements, OpenAI's speech API. It was called `openai-compatible` until 2026-11-16, and that name still works but logs a deprecation warning. Install it with the `openai-compatible` extra, the one extra shared by the OpenAI-protocol modules:

```bash
uv sync --no-dev --extra openai-compatible
# For an installed package:
pip install "tts-engine[openai-compatible]"
```

```json
{
  "module": {
    "type": "openai-speech-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "voice": "marin"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | no | — | Name of the environment variable holding the API key; set it to `OPENAI_API_KEY` for OpenAI's own endpoint. Absent, no key is read and a placeholder is sent, for a compatible server that needs none. The key itself is never written in the config: a config with `api_key` is refused. |
| `voice` | yes | — | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin` or `cedar`, or a compatible server's voice name. |
| `model` | no | `gpt-4o-mini-tts-2025-03-20` | Speech model ID, e.g. `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd`, or a compatible server's model. The default snapshot is pinned because the `gpt-4o-mini-tts` alias's latency is erratic. |
| `instructions` | no | — | Voice direction such as tone, pace or accent. Used by `gpt-4o-mini-tts`; ignored by `tts-1` and `tts-1-hd`. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier, `0.25`–`4.0`. |
| `base_url` | no | OpenAI | Base URL of an OpenAI-compatible server, including `/v1`, e.g. `http://localhost:8000/v1`. |
| `sample_rate` | no | `24000` | The rate of the server's `pcm` output. OpenAI's is always 24,000 Hz; set it only with `base_url`, to match the server. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at `sample_rate`. A text stream is synthesized one request per sentence. Time to first audio differs by model: in the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md) (one short sentence, median of 20), the default `2025-03-20` snapshot answers in 367 ms, the `gpt-4o-mini-tts` alias in 516 ms, `tts-1` in 1.1 s and `tts-1-hd` in 2.0 s. The alias has also stalled for tens of seconds on other days, which is why the default is pinned to the snapshot. OpenAI has announced the shutdown of these speech models for 2027-01-06; after that date the module serves compatible servers only. Their successor, OpenAI's Realtime API, is the [`openai`](openai.md) module.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` is set in the process running the engine. Without `api_key_env`, no key is sent: OpenAI's own endpoint then answers 401.
- `does not take 'api_key'` means the config still writes the key itself: move it to an environment variable and name that variable in `api_key_env`.
- A `TTSError` reporting `Error code: 401` means the key was rejected; `Error code: 400` with `Invalid value` lists the supported voices; `model_not_found` (404) means `model` is wrong.
- `OpenAI-compatible server returned WAV, not raw pcm` means the server behind `base_url` ignores `response_format: "pcm"`. That server cannot be used with this module.
- `TTS module type 'openai-compatible' is deprecated` in the log means the config still uses the old name: set `"type": "openai-speech-compatible"`. Nothing else changes.
- If speech from a compatible server sounds too fast, too slow or pitch-shifted, set `sample_rate` to the server's output rate.

## See also

- [specs/modules/openai-speech-compatible.md](../../specs/modules/openai-speech-compatible.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/configs/openai-speech-compatible.json](../../examples/configs/openai-speech-compatible.json): a complete engine config.
- [Latency benchmark](../../benchmarks/README.md#results): measured time to first audio for each of its models.
