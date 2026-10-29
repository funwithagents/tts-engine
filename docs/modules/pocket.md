# `pocket`

**Kind:** `local` · **Synthesis:** In-process model · **Install:** `tts-engine[pocket]` · **Credentials:** None · **Voice listing:** yes

The pocket module runs [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) in process. It needs no API key and makes no network request during synthesis after its model weights are available. The first use may download weights into the Hugging Face cache.

Install the optional dependencies:

```bash
uv sync --no-dev --extra pocket
# For an installed package:
pip install "tts-engine[pocket]"
```

Then select the module:

```json
{
  "module": {
    "type": "pocket",
    "voice": "alba",
    "language": "english",
    "device": "auto"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | The model's default voice (`alba` for English, `estelle` for French, …) | Preset name, a local audio file (`.wav`, `.mp3`, `.flac`) or `.safetensors` voice state (relative to `base_dir`), or an `hf://`/`https://` URL to one. Cloning from audio needs the gated weights (below). |
| `language` | no | pocket-tts default (`english`, the `english_2026-10` model) | The model: `english`, `french`, `german`, `italian`, `portuguese`, `spanish`, `dutch`, a `<language>_24l` variant, a dated English model such as `english_2026-09`, or a tiny one: `english_18m_2026-10`, `english_7m_2026-10`, `english_2m_2026-10`. Not together with `config`. |
| `config` | no | — | A custom model config YAML (`hf://…/file.yaml`, `https://…`, or a local path relative to `base_dir`) for community or fine-tuned weights. Preset voices do not work with it; use an audio voice. |
| `device` | no | `auto` | `auto`, `cpu`, `cuda`, or experimental `mps`. `auto` selects CUDA when available and otherwise CPU, and always CPU with `quantize`. |
| `quantize` | no | `false` | Int8 quantization of the transformer: about half the memory and faster on x86 CPUs. CPU only. |
| `temperature` | no | Model default (0.3) | Sampling temperature. |
| `max_tokens` | no | pocket-tts default | Per-text-chunk token cap. Increase only for unusually long unbroken text. |

The module emits mono PCM at the model's native sample rate, 24,000 Hz. On Apple Silicon, `auto` intentionally uses CPU; `mps` remains explicit and experimental.

Preset voices work out of the box. Cloning a voice from an audio file needs the gated weights: accept the terms at [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts), then set `HF_TOKEN` (or run `hf auth login`). Without them, an audio voice is rejected at construction with a `ConfigError`.

## Troubleshooting

- Ensure the `pocket` extra is installed.
- Allow network access and sufficient disk space when model weights are downloaded for the first time.
- On Apple Silicon, keep `device` set to `auto` or `cpu` unless explicitly testing the experimental `mps` path.
- A `ConfigError` starting with `Pocket module:` is pocket-tts rejecting a value: an unknown `language`, a missing file, a preset voice with a custom `config`, or an audio voice without the gated cloning weights.

## See also

- [specs/modules/pocket.md](../../specs/modules/pocket.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.pocket.json](../../examples/config.pocket.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
