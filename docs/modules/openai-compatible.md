# `openai-compatible`

**Kind:** `hosted` · **Synthesis:** OpenAI's `/v1/audio/speech` (`gpt-4o-mini-tts`, `tts-1`) or an OpenAI-compatible server · **Install:** `tts-engine[openai-compatible]` · **Credentials:** API key · **Voice listing:** no

The OpenAI-compatible module streams speech from a `/v1/audio/speech` endpoint through the official `openai` SDK: OpenAI's own by default, or, with `base_url`, any OpenAI-compatible speech server that returns raw `pcm` (vLLM-Omni, Kokoro-FastAPI, `mlx_audio.server`, …). Install it with the `openai-compatible` extra:

```bash
uv sync --no-dev --extra openai-compatible
# For an installed package:
pip install "tts-engine[openai-compatible]"
```

```json
{
  "module": {
    "type": "openai-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "voice": "marin"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. For a compatible server that needs no key, use any placeholder, e.g. `"unused"`. |
| `voice` | yes | — | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin` or `cedar`, or a compatible server's voice name. |
| `model` | no | `gpt-4o-mini-tts-2025-03-20` | Speech model ID, e.g. `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd`, or a compatible server's model. The default snapshot is pinned because the `gpt-4o-mini-tts` alias's latency is erratic. |
| `instructions` | no | — | Voice direction such as tone, pace or accent. Used by `gpt-4o-mini-tts`; ignored by `tts-1` and `tts-1-hd`. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier, `0.25`–`4.0`. |
| `base_url` | no | OpenAI | Base URL of an OpenAI-compatible server, including `/v1`, e.g. `http://localhost:8000/v1`. |
| `sample_rate` | no | `24000` | The rate of the server's `pcm` output. OpenAI's is always 24,000 Hz; set it only with `base_url`, to match the server. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at `sample_rate`. A text stream is synthesized one request per sentence. Time to first audio differs by model: in the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md) (one short sentence, median of 20), the default `2025-03-20` snapshot answers in 367 ms, the `gpt-4o-mini-tts` alias in 516 ms, `tts-1` in 1.1 s and `tts-1-hd` in 2.0 s. The alias has also stalled for tens of seconds on other days, which is why the default is pinned to the snapshot. OpenAI has announced the shutdown of these speech models for 2027-01-06; after that date the module serves compatible servers only. Their successor, OpenAI's Realtime API, is the [`openai`](openai.md) module.

## Troubleshooting

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting `Error code: 401` means the key was rejected; `Error code: 400` with `Invalid value` lists the supported voices; `model_not_found` (404) means `model` is wrong.
- `OpenAI-compatible server returned WAV, not raw pcm` means the server behind `base_url` ignores `response_format: "pcm"`. That server cannot be used with this module.
- If speech from a compatible server sounds too fast, too slow or pitch-shifted, set `sample_rate` to the server's output rate.

## See also

- [specs/modules/openai-compatible.md](../../specs/modules/openai-compatible.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.openai-compatible.json](../../examples/config.openai-compatible.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
