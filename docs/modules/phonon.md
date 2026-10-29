# `phonon`

**Kind:** `local` · **Synthesis:** In-process model · **Install:** `tts-engine[phonon]` · **Credentials:** None · **Voice listing:** yes

The phonon module runs Gradium's open-weight [Phonon](https://gradium.ai/phonon) models in process through the [`ptts`](https://github.com/gradium-ai/xn-ptts) runtime. It needs no API key and no PyTorch, installs from wheels on Linux, macOS (Apple Silicon and Intel) and Windows, and runs much faster than real time, on a CPU too. In the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md) (an Apple M5 Pro), the default model's first audio comes after 13 ms (median) on Metal and 18 ms on the CPU. The first use downloads the model (about 160 MB) and any catalog voice into the Hugging Face cache.

Install the optional dependencies:

```bash
uv sync --no-dev --extra phonon
# For an installed package:
pip install "tts-engine[phonon]"
```

Then select the module:

```json
{
  "module": {
    "type": "phonon",
    "voice": "Juniper"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `language` | no | `en` | Text normalization: `en`, `fr`, `de`, `es`, `pt`, or `none`. It does not select the model. |
| `model` | no | `hf://gradium/phonon-31m-en` | `hf://owner/name[@revision]`, or a local model directory (relative to `base_dir`). |
| `voice` | no | The model's `default` voice | A Phonon catalog voice name such as `Juniper`, `Harper` or `Miles`, or a local `.safetensors` voice file (relative to `base_dir`). |
| `device` | no | `auto` | `auto` picks the first device the installed build offers (Metal on Apple Silicon, CPU elsewhere), or `cpu`, `metal`. |
| `quant` | no | Model weights | CPU-only weight format, e.g. `q8_0` or `q4k`. |
| `temperature` | no | `ptts` default (0.3) | Sampling temperature, 0 or more. |
| `cfg_coef` | no | Model default | Guidance coefficient (voice adherence). |

The module emits mono PCM at 24,000 Hz. The default English model is Apache-2.0. The French, German, Spanish, Portuguese and multilingual models (`gradium/phonon-40m-*`) are gated and licensed CC BY-NC 4.0: they need an `HF_TOKEN` with access, plus matching `model` and `language` fields.

## Troubleshooting

- Ensure the `phonon` extra is installed.
- Allow network access when the model or a catalog voice is downloaded for the first time; a `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable or a gated model was requested without an `HF_TOKEN`.
- A `ConfigError` naming a device or `quant` means the installed `ptts` build does not support that combination. Quantization works on CPU only.

## See also

- [specs/modules/phonon.md](../../specs/modules/phonon.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.phonon.json](../../examples/config.phonon.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
