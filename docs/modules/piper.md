# `piper`

**Kind:** `local` · **Synthesis:** In-process model · **Install:** `tts-engine[piper]` · **Credentials:** None · **Voice listing:** yes

The piper module runs [Piper](https://github.com/OHF-Voice/piper1-gpl) voices in process through the `piper-tts` package: small VITS models on onnxruntime, with espeak-ng built in. It needs no API key and no PyTorch, and it is light: in the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md) (an Apple M5 Pro, CPU), the default voice's first audio comes after 67 ms (median), and the `high` voices' after 331–377 ms. On a Raspberry Pi 4 at 1.5 GHz (result [`20261029-1124_pi4-1500-nodaemon`](../../benchmarks/results/20261029-1124_pi4-1500-nodaemon/findings.md)), the default voice's first audio comes after 1.2 s and `en_US-lessac-low`'s after 0.8 s, and both then run at 3–4 times real time without a stall. There are 178 voices in 58 locales in [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) ([samples](https://rhasspy.github.io/piper-samples/)). They sound less natural than Kokoro. The first use of a voice downloads it into the Hugging Face cache (63 MB for the default). It installs on Linux (x86_64 and arm64), Apple-silicon macOS and Windows, but not on Intel Macs, which have no onnxruntime wheel.

```bash
uv sync --no-dev --extra piper
# For an installed package:
pip install "tts-engine[piper]"
```

Then select the module:

```json
{
  "module": {
    "type": "piper",
    "voice": "en_US-ljspeech-medium"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | `en_US-ljspeech-medium` | A voice key from rhasspy/piper-voices, `<locale>-<name>-<quality>`: `en_GB-alba-medium`, `fr_FR-siwis-medium`, `de_DE-thorsten-high`, … Not with `model`. |
| `model` | no | None | A voice outside the catalog: `hf://owner/repo/path/voice.onnx[@revision]` or a local `.onnx` path (relative to `base_dir`), with its `.onnx.json` next to it. Not with `voice`. |
| `speaker` | no | The voice's default | For a multi-speaker voice (`en_US-libritts_r-medium`, `en_US-arctic-medium`): a speaker name or index. |
| `speed` | no | The voice's own pace | Speaking rate, from 0.5 to 2.0. |
| `threads` | no | onnxruntime's choice (the physical cores) | CPU threads for synthesis, 1 or more. Fewer threads leave cores for the rest of the machine at some latency: two threads doubled the time per sentence on an M5 Pro. |

Catalog voices are pinned to one commit of the repository. The module emits mono PCM at the voice's rate: 22,050 Hz for `medium` and `high` voices, 16,000 Hz for `low` ones. It synthesizes one sentence at a time and prepares the next one while the current one plays. The Japanese, Thai and two of the Chinese voices (`zh_CN-chaowen-medium`, `zh_CN-xiao_ya-medium`) need extra packages and are not supported; the module says so when it loads them.

**Voice licenses.** Each voice is trained on a dataset with its own license, named in the voice's `MODEL_CARD` on Hugging Face. Read it before you ship a voice. For example, `en_US-ljspeech-*` (the default), `bryce`, `john`, `kristin` and `norman` are public domain; `en_US-libritts_r-medium`, `en_GB-alba-medium` and `fr_FR-siwis-medium` are CC BY 4.0 (credit required). The popular `en_US-lessac-*` voices come from a dataset under a *research* license.

**License note.** `piper-tts` is GPL-3.0-or-later, with espeak-ng (GPL-3.0) built in. Using it, locally or on a server, is not restricted. If you redistribute an application that includes this extra, check what the GPL requires of you.

## Troubleshooting

- Ensure the `piper` extra is installed. It does not install on Intel Macs.
- A `ConfigError` starting with `Piper module: 404` means the `voice` key is not in the catalog; check the spelling against [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).
- A `TTSError` reporting a model load failure on first use usually means the Hugging Face Hub was unreachable and the voice is not cached yet.
- A `ConfigError` naming `speaker` lists how many speakers the voice has; a single-speaker voice takes no `speaker`.

## See also

- [specs/modules/piper.md](../../specs/modules/piper.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.piper.json](../../examples/config.piper.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/README.md#results): measured time to first audio for each of its models.
