# `openai`

**Kind:** `hosted` · **Synthesis:** OpenAI Realtime API (`gpt-realtime-2.1-mini`) · **Install:** `tts-engine[openai]` · **Credentials:** API key · **Voice listing:** no

The OpenAI module speaks through OpenAI's Realtime API (`gpt-realtime-2.1-mini`), the successor of the speech models OpenAI shuts down on 2027-01-06, over the official `openai` SDK's realtime WebSocket. Install it with the `openai` extra:

```bash
uv sync --no-dev --extra openai
# For an installed package:
pip install "tts-engine[openai]"
```

```json
{
  "module": {
    "type": "openai",
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `model` | no | `gpt-realtime-2.1-mini` | Realtime model ID, e.g. `gpt-realtime-2.1`. |
| `voice` | no | `marin` | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin` or `cedar`. |
| `instructions` | no | — | Voice direction such as tone, pace or accent, added to the module's instruction to read the text verbatim. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier, `0.25`–`1.5`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails. |

The module emits mono PCM at 24,000 Hz. The Realtime API is a conversation model, so the module asks it to read each text aloud word for word. In testing it did, including for questions and instructions inside the text, but nothing guarantees it. The module opens one connection when it is constructed and keeps it for its lifetime, so the handshake is paid once, not on every `say()`. A long text is sent in parts, and a text stream is spoken one sentence at a time. In the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md), the first audio of one short sentence comes after 450 ms (median) with `gpt-realtime-2.1-mini` and 942 ms with `gpt-realtime-2.1`. Billing is per audio token, about $0.025 per minute of speech with `gpt-realtime-2.1-mini`.

## See also

- [specs/modules/openai.md](../../specs/modules/openai.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.openai.json](../../examples/config.openai.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
