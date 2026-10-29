# `kokoro`

**Kind:** `local` · **Synthesis:** In-process model · **Install:** `tts-engine[kokoro]` · **Credentials:** None · **Voice listing:** yes

The kokoro module runs [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0) in process through [`kokoro-onnx`](https://github.com/thewh1teagle/kokoro-onnx): onnxruntime for the model, and an espeak-ng that ships with the package for pronunciation. It needs no API key and no PyTorch, and it has 54 voices in nine languages: American and British English, Spanish, French, Hindi, Italian, Japanese, Brazilian Portuguese and Mandarin. In the latency benchmark's result [`20261027-1453_mac-m5-pro`](../../benchmarks/results/20261027-1453_mac-m5-pro/summary.md) (an Apple M5 Pro, CPU), the default model's first audio comes after 527 ms (median); the int8 export from the kokoro-onnx releases halves that, to 253 ms. The first use downloads about 350 MB into the Hugging Face cache. It installs on Linux (x86_64 and arm64), Apple-silicon macOS and Windows, but not on Intel Macs, which have no onnxruntime wheel.

It uses `kokoro-onnx` rather than hexgrad's official `kokoro` package because the official package needs PyTorch and does not support Python 3.13. The trade-off is English pronunciation: `kokoro-onnx` uses espeak-ng directly instead of the official package's misaki dictionary.

```bash
uv sync --no-dev --extra kokoro
# For an installed package:
pip install "tts-engine[kokoro]"
```

Then select the module:

```json
{
  "module": {
    "type": "kokoro",
    "voice": "af_heart"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | `af_heart` | A voice from the voices file. The first letter is the language (`a` American English, `b` British English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `j` Japanese, `p` Brazilian Portuguese, `z` Mandarin), the second the gender: `af_heart`, `am_michael`, `bf_emma`, `ff_siwis`, … The full list is in [Kokoro's VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). |
| `language` | no | From the voice's first letter | An espeak-ng language code: `en-us`, `en-gb`, `es`, `fr-fr`, `hi`, `it`, `ja`, `pt-br`, `cmn`. |
| `speed` | no | Library default (1.0) | Speaking rate, from 0.5 to 2.0. |
| `threads` | no | onnxruntime's choice (the physical cores) | CPU threads for synthesis, 1 or more. Fewer threads leave cores for the rest of the machine at some latency: two threads doubled the time to first audio on an M5 Pro. |
| `model` | no | `kokoro-v1.0.onnx`, pinned | `hf://owner/repo/path/file.onnx[@revision]`, or a local path (relative to `base_dir`), e.g. the int8 or fp16 exports from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases). |
| `voices` | no | `voices-v1.0.bin`, pinned | Same forms as `model`, for the voices file. |

The default files are kokoro-onnx's own release files, from the `fastrtc/kokoro-onnx` Hugging Face repository pinned to one commit.

The module emits mono PCM at 24,000 Hz. It synthesizes one sentence per call, for a complete text as well as a text stream, and synthesizes the next sentence while the current one plays, so there is no gap between sentences.

**License note.** The model, `kokoro-onnx` and onnxruntime are permissively licensed, but the pronunciation step is not: `phonemizer` is GPLv3+ and the bundled espeak-ng is GPL-3.0. The official `kokoro` package uses espeak-ng too. If you redistribute an application that includes this extra, check what the GPL requires of you.

## Troubleshooting

- Ensure the `kokoro` extra is installed. It does not install on Intel Macs or on Python 3.14, because `kokoro-onnx` does not support them yet.
- Allow network access for the first download (about 350 MB). A `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable and the files are not cached yet.
- A `ConfigError` naming `voice` means the voice is not in the voices file. A `ConfigError` naming `language` means espeak-ng does not know that code. A custom voice whose first letter is not a known language needs an explicit `language`.
- The int8 model returns no audio with `threads: 1` (a `TTSError` about an empty array): give it 2 or more.

## See also

- [specs/modules/kokoro.md](../../specs/modules/kokoro.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.kokoro.json](../../examples/config.kokoro.json): a complete MCP-server config.
- [Latency benchmark](../../benchmarks/RESULTS.md): measured time to first audio for each of its models.
