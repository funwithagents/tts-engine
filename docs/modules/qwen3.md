# `qwen3`

**Kind:** `local` · **Synthesis:** In-process model (Apple silicon) · **Install:** `tts-engine[qwen3]` · **Credentials:** None · **Voice listing:** yes

The qwen3 module runs [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache-2.0) in process through [`qwentts-cpp-python`](https://github.com/andimarafioti/qwentts-cpp-python), bindings over the qwentts.cpp GGML engine. It needs no API key and no PyTorch, speaks ten languages with nine preset voices, and can design a voice from a text description. On an Apple-silicon Mac it runs on Metal at about 3–4× real time, with first audio in about 0.2 s. The first use downloads the GGUF weights into the Hugging Face cache: about 1.3 GB for the default 0.6B model at `Q8_0`, about 2.3 GB for a 1.7B model.

The extra installs on **Apple-silicon macOS 14+ only**, because PyPI has no CPU or Windows build of the runtime. On Linux with an NVIDIA GPU, glibc 2.39+ and the CUDA 12.8 runtime, install the CUDA wheel yourself (`pip install "qwentts-cpp-python>=0.5,<0.6"`); that path is not tested here.

```bash
uv sync --no-dev --extra qwen3
# For an installed package:
pip install "tts-engine[qwen3]"
```

Then select the module:

```json
{
  "module": {
    "type": "qwen3",
    "voice": "ryan",
    "language": "english"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `model` | no | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | `Qwen/Qwen3-TTS-12Hz-{0.6B,1.7B}-CustomVoice` (preset voices) or `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` (a voice described in `instruct`). The `-Base` cloning models are not supported. |
| `quant` | no | `Q8_0` | Weight format: `Q8_0`, `Q4_K_M`, `BF16` or `F32`. |
| `voice` | no | `aiden` | A preset voice: `aiden`, `ryan`, `vivian`, `serena`, `uncle_fu`, `dylan`, `eric`, `ono_anna`, `sohee` (case-insensitive). Not allowed with a VoiceDesign model. |
| `instruct` | VoiceDesign only | None | Style instruction for CustomVoice ("Speak cheerfully."), or the voice description for VoiceDesign. Qwen documents it for the 1.7B models. |
| `language` | no | `auto` | `auto` or `english`, `chinese`, `french`, `german`, `spanish`, `italian`, `portuguese`, `russian`, `japanese`, `korean`. |
| `temperature` | no | Library default (0.9) | Sampling temperature, greater than 0. |

The module emits mono PCM at 24,000 Hz and synthesizes one sentence per call, for a complete text as well as a text stream.

## Troubleshooting

- Ensure the `qwen3` extra is installed, on an Apple-silicon Mac (on any other platform the extra installs nothing and the module reports it missing).
- Allow network access and several gigabytes of disk for the first download; a `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable, or, on Linux, that the CUDA runtime is missing.
- A `ConfigError` listing voices or languages names the values the loaded model accepts.

## See also

- [specs/modules/qwen3.md](../../specs/modules/qwen3.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.qwen3.json](../../examples/config.qwen3.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/latency/RESULTS.md): measured time to first audio for each of its models.
