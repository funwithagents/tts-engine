---
code:
  - src/tts_engine/modules/qwen3/__init__.py
  - src/tts_engine/modules/qwen3/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_qwen3.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Qwen3 Module

**Status:** Implemented

## Overview

`qwen3` implements `TTSModule` using [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache-2.0, 0.6B and 1.7B, ten languages, preset speakers, voice design and voice cloning), run in process through [`qwentts-cpp-python`](https://github.com/andimarafioti/qwentts-cpp-python): ctypes bindings over the [qwentts.cpp](https://github.com/Serveurperso/qwentts.cpp) GGML engine, with GGUF weights from the Hugging Face Hub. It is a **local-model** backend following [tts-module-interface.md](../tts-module-interface.md) ("Local-model modules"), the same shape as [phonon.md](phonon.md): the library is wrapped directly, inference runs in the cancellable worker, float waveforms are converted to signed-16-bit PCM before the callback, and text is synthesized sentence by sentence.

### Why `qwentts-cpp-python` and not another runtime

Three runtimes stream Qwen3-TTS audio today; the module uses the lightest one that reaches a GPU on both macOS and Linux.

- **`faster-qwen3-tts`** (what [huggingface/speech-to-speech](https://github.com/huggingface/speech-to-speech) uses off macOS). Its torch backend runs on CUDA only (`from_pretrained` raises "CUDA graphs require CUDA device" otherwise). Its `ggml` backend is a thin adapter over `qwentts_cpp.QwenTTS.stream()`, but the package still requires `torch`, `transformers>=5.15` and `qwen-tts-hf` (which pulls `gradio`, `librosa`, `torchaudio`, `onnxruntime`, `sox`), none of which the GGML path uses. Depending on it would cost gigabytes for an adapter.
- **`mlx-audio`** (what speech-to-speech uses on macOS). Apple-silicon only, a second runtime for the same model, and better served by a future generic `mlx` module covering every model mlx-audio runs.
- **`qwentts-cpp-python`**: depends on `numpy` and `huggingface-hub` only, streams audio with a native cancel callback, and is the engine `faster-qwen3-tts[ggml]` itself drives. Chosen.

The [Reachy Mini conversation app](https://github.com/pollen-robotics/reachy_mini_conversation_app) does not run the model: it talks to a deployed speech-to-speech server. It contributes only its catalog (nine CustomVoice speakers, ten languages), which this spec matches.

### Platforms (the constraint that shapes this module)

`qwentts-cpp-python` 0.5.0 publishes three wheels on PyPI, and no sdist:

| Wheel | Backend | Runs where |
|---|---|---|
| `macosx_14_0_arm64` (1.2 MB) | Metal + CPU + BLAS | Apple-silicon Macs on macOS 14+ |
| `1cu128-manylinux_2_39_x86_64` / `_aarch64` (~96 MB each) | CUDA 12.8 | Linux with an NVIDIA GPU and the CUDA 12.8 runtime installed (not bundled) |

There is no Windows wheel, no Intel-macOS wheel and no CPU-only Linux wheel on PyPI (CPU and other CUDA builds of older versions live on a Hugging Face `--find-links` page only). So the `qwen3` extra is declared **for macOS arm64 only**; on Linux with a GPU, a user installs the CUDA wheel by hand (below). The fast tier fakes the library and runs everywhere; the live row runs on the development Mac and skips in CI (no wheel on the Linux runner, no GPU there either).

## Config fields

All fields go under the `engine.module` block alongside `"type": "qwen3"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | non-empty string | no | `"Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"` | A Qwen3-TTS model id: `Qwen/Qwen3-TTS-12Hz-{0.6B,1.7B}-CustomVoice` (preset speakers) or `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` (a voice described in `instruct`). The library maps the id to its GGUF pair in `Serveurperso/Qwen3-TTS-GGUF` and downloads it to the Hugging Face cache. The `-Base` models (voice cloning) are rejected in v1 (see "Open questions"). |
| `quant` | non-empty string | no | `"Q8_0"` | GGUF weight format: `Q8_0`, `Q4_K_M`, `BF16` or `F32` (the library also accepts `Q8`, `Q4`, `FP32`; case-insensitive). |
| `voice` | non-empty string | no | `"aiden"` for a CustomVoice model | A preset speaker: `aiden`, `ryan`, `vivian`, `serena`, `uncle_fu`, `dylan`, `eric`, `ono_anna`, `sohee`, matched case-insensitively against the loaded model's own list. Passed to the library as `speaker`. Must be absent for a VoiceDesign model. |
| `instruct` | non-empty string | VoiceDesign: yes; CustomVoice: no | — | Natural-language style control. For VoiceDesign it *is* the voice ("A calm, warm female narrator."); for CustomVoice it steers delivery ("Speak cheerfully."). Qwen documents instruction control for the 1.7B models; the 0.6B CustomVoice model accepts the field without error. |
| `language` | non-empty string | no | `"auto"` | `auto` (the model detects it from the text) or one of the loaded model's languages: `english`, `chinese`, `french`, `german`, `spanish`, `italian`, `portuguese`, `russian`, `japanese`, `korean`, plus `beijing_dialect`, `sichuan_dialect`. Case-insensitive, sent lowercase. |
| `temperature` | finite number > 0 | no | — (library default 0.9) | Sampling temperature. Must be > 0: the library treats 0 as greedy decoding through a separate flag, which v1 does not expose. |

There is no API key: weights come from the public Hub repository (Apache-2.0). Fields left unset — `temperature` — are **omitted** from the library call so its own default applies. `model`, `quant`, `voice` and `language` have module defaults because the library either has none (`model`, `voice`) or one that does not fit an engine (see below).

### Defaults, and why

- **`model`: 0.6B CustomVoice.** Measured on an Apple M5 Pro (Metal), warm: 0.6B Q8_0 reaches the first audio in ~0.17 s at ~3.8× real time; 1.7B Q8_0 in ~0.19 s at ~3.2×; 1.7B BF16 in ~0.26 s at ~2.3×. The 0.6B model is the smallest download (≈1.26 GB for Q8_0 talker + codec) with preset voices, so it is the zero-config choice; 1.7B is one field away. speech-to-speech defaults to 1.7B for quality on a GPU server.
- **`quant`: `Q8_0`, not the library's `BF16`.** Q8_0 is half the download (969 MB vs 1.8 GB for the 0.6B talker) and faster on Metal, with no audible loss reported upstream.
- **`voice`: `aiden`.** A CustomVoice model has no default speaker (the library fails at synthesis with "custom_voice models require --speaker"); `aiden` (American English, male) is speech-to-speech's default.
- **`language`: `auto`.** The engine speaks whatever text it is given, in any of the ten languages; `auto` is speech-to-speech's default too. The library's own default (`english`) would mispronounce every other language.

### Validation

At construction, **before** the model is loaded (so a bad config never costs a multi-gigabyte download):

- `model`, `quant`, `voice`, `instruct`, `language` — if present, non-empty strings; `temperature` — if present, a finite number > 0, booleans, `NaN` and the infinities rejected.
- The model kind is read from the id's suffix: `-Base` → `ConfigError` (voice cloning is not supported in v1); `-VoiceDesign` without `instruct`, or with `voice` → `ConfigError`. Any other suffix is treated as CustomVoice and left to the library to accept or reject.

After loading, against the loaded model:

- `voice` must be in `tts.speaker_names()` (case-insensitive), else `ConfigError` listing the available speakers. The library itself only fails on the first synthesis, with a generic status `-3`, so this check is the module's.
- `language` must be `auto` or in `tts.language_names()` (case-insensitive), else `ConfigError` listing them.

On either post-load failure the module closes the loaded `QwenTTS` before raising, so the native context and its GPU memory are freed at once.

## Implementation notes

### Dependencies and lazy import

Requires the `qwen3` extra: `pip install tts-engine[qwen3]` / `uv sync --extra qwen3`, declared as

```toml
qwen3 = [
    "qwentts-cpp-python>=0.5,<0.6; sys_platform == 'darwin' and platform_machine == 'arm64'",
]
```

and added to `all`. The upper bound is deliberate: the bindings pin one native qwentts.cpp revision (`QWENTTS_NATIVE_REVISION`, ABI v5) and refuse any other, the package is alpha, and the module's calls are validated against 0.5.0 only. The marker keeps `uv sync --dev` (whose `all` includes `qwen3`) working on Linux and Windows, where it installs nothing.

**Linux with an NVIDIA GPU.** Install the CUDA 12.8 wheel next to the base package: `pip install tts-engine "qwentts-cpp-python>=0.5,<0.6"` on a glibc ≥ 2.39 system with the CUDA 12.8 runtime and cuBLAS present. The module needs nothing else. This path is documented but not verified by this project (no GPU machine); see "Open questions".

`qwen3/module.py` must not import `qwentts_cpp` at file top (the registry imports every module class). The import happens inside `__init__`, with the `# pyright: ignore[reportMissingImports]` comment because the static gate runs on Linux, where the extra installs nothing:

```python
try:
    import qwentts_cpp  # pyright: ignore[reportMissingImports]
except ImportError as exc:
    raise ConfigError(
        "The 'qwen3' module requires the qwen3 extra: pip install tts-engine[qwen3] "
        "(Apple-silicon macOS; on Linux with CUDA 12.8, pip install qwentts-cpp-python)"
    ) from exc
```

### Construction (eager model load)

The engine reads `sample_rate` once at construction, so the model is loaded in `__init__`, after validation and the lazy import:

1. `tts = qwentts_cpp.QwenTTS.from_pretrained(model, quant=quant, log_level="warning")`. Weights download to the Hugging Face cache on first use (≈1.26 GB for the default, ≈2.3 GB for 1.7B Q8_0) and load in ~3 s from the cache afterwards. The device is the build's own: Metal on the macOS wheel, CUDA on the Linux wheel; there is no device field.
2. `log_level="warning"` filters the native qwentts.cpp and GGML logs process-wide. Without it they print per-frame `[Pipeline]` and `[Perf]` lines to stderr. The module does not forward them into `logging` (see "Open questions").
3. Post-load validation (above). Record `self._tts`, the canonical (lowercase) speaker or `None`, the lowercase language, `instruct`, and the sampling kwargs.

`sample_rate` returns **24000**: Qwen3-TTS's 12 Hz codec decodes 1920-sample frames at 24 kHz on every model, and the library yields `(chunk, 24000)`. The module checks the yielded rate against 24000 and raises `TTSError` on a mismatch rather than playing at the wrong speed.

### Streaming and format conversion

`tts.stream(text=..., lang=..., speaker=..., instruct=..., temperature=...)` is a **generator** of `(numpy.float32 1-D chunk, 24000)` tuples. A daemon producer thread runs the native `qt_synthesize` and queues packets: the first packet is 4 codec frames (320 ms), later ones 8 frames (640 ms) by default, plus a short final flush. Closing the generator (`close()`, or leaving a `contextlib.closing` block) sets the native cancel flag, which `qt_synthesize` polls per frame, and joins the producer (timeout 1 s); measured: 3.6 ms mid-generation.

The generate-and-feed loop for one sentence, shared by both input shapes:

```python
def _generate(self, text, callback, stop):
    chunks = self._tts.stream(text=text, lang=self._language, **self._voice_kwargs)
    with contextlib.closing(chunks):  # closing cancels the native synthesis
        while not stop.is_set():
            try:
                chunk, rate = next(chunks)
            except StopIteration:
                break
            except Exception as exc:
                raise TTSError(f"qwen3 synthesis failed: {exc}") from exc
            if rate != 24000:
                raise TTSError(...)
            callback(float_to_int16_pcm(chunk))
```

`_voice_kwargs` holds only what applies: `speaker` (CustomVoice), `instruct` (when set), `temperature` (when set). The conversion is the shared `float_to_int16_pcm` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers"): flatten, clip to `[-1, 1]`, scale by 32767, little-endian `int16`. Peak amplitude measured ~0.59, so clipping is a guard, not a gain stage.

The `QwenTTS` object is built on the constructing thread and used from the worker; verified on 0.5.0 (`QwenTTS` serializes synthesis with its own lock, and the producer thread is the library's).

### Both input shapes: sentence grouping

qwentts.cpp has no incremental text input (the `faster-qwen3-tts` GGML adapter warns that "step-by-step text feeding" is not exposed). `synthesize_text_stream` therefore runs one worker from `run_text_stream_worker`, iterating `segment_sentences(pieces)` and calling `_generate` per sentence, as phonon does.

Unlike phonon, **`synthesize_text` segments too**: it runs `_generate` over `segment_sentences([text])`. Qwen3-TTS is autoregressive with a per-call cap (`max_new_tokens`, library default 2048 frames ≈ 164 s), and long prompts drift; speech-to-speech likewise only ever sends it sentence-sized text. A sentence per call keeps every call far from the cap, and the warm per-call cost (~0.17 s to first audio) is paid while the previous sentence is still playing, except for the first.

### Cancellation

Cooperative, as for every module: `stop` is polled between packets, and the generator is closed on every path through the shared `cleanup` helper — which cancels and joins the native synthesis before `_generate` returns or raises, and never lets a failing close replace the utterance's own error. `next()` blocks until the next packet, so a stop set mid-packet is seen once that packet is ready: at most one packet's generation time (≈0.2 s at 3× real time for a 640 ms packet). No callback can occur after the coroutine exits.

### Error handling

At construction:

- A missing extra → `ConfigError` with the install hint (above).
- A field failing the module's own validation (pre- or post-load) → `ConfigError`.
- `ValueError` from `from_pretrained` — an unknown model id or quant, which the library checks before any download — → `ConfigError(f"Qwen3 module: {exc}")`, chained.
- Any other exception while loading — `LibraryNotFoundError`/`QwenTTSError` (the native library missing, the wrong ABI revision, the CUDA runtime absent, a corrupt GGUF), or a Hub/network error — → `TTSError(f"qwen3 model load failed: {exc}")`, chained.

At synthesis:

- Any exception from advancing the stream → `TTSError(f"qwen3 synthesis failed: {exc}")`, chained. `stream()` is a generator, so even the library's argument checks surface on the first `next`.
- Exceptions raised by `callback` propagate unchanged; the generator is still closed.
- Text-stream failures propagate unchanged through `run_text_stream_worker`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` calls the runtime's own `close()` (freeing the native context and any GPU memory at once, the same call the constructor makes when a post-load check fails) and drops it; the module is not usable afterwards.

## Module ID

Registered in `REGISTRY` as `"qwen3"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)); class `Qwen3Module` in `modules/qwen3/module.py`, re-exported from `modules/qwen3/__init__.py`.

## Example config

`examples/config.qwen3.json`:

```json
{
  "engine": {
    "module": {
      "type": "qwen3",
      "voice": "ryan",
      "language": "english"
    },
    "player": { "device": null }
  },
  "server": { "host": "127.0.0.1", "port": 8000 }
}
```

## Testing

- **Unit (`tests/modules/test_qwen3.py`)** — `qwentts_cpp` is **faked** (a stub module with `QwenTTS.from_pretrained` and `QwenTTSError`, injected through `monkeypatch.setitem(sys.modules, "qwentts_cpp", ...)`), so the fast tier never loads a model, touches the Hub, or needs the macOS wheel. The fake `QwenTTS` records its load arguments, lists speakers and languages, and its `stream()` is a generator over given float32 arrays that records the call kwargs and whether it was closed. Covers:
  - `ConfigError` with the install hint when `qwentts_cpp` is unimportable;
  - pre-load validation: empty/non-string fields, non-positive, boolean or non-finite `temperature`, a `-Base` model, VoiceDesign without `instruct` or with `voice` — none of which reaches `from_pretrained`;
  - load arguments: defaults (`0.6B-CustomVoice`, `Q8_0`, `log_level="warning"`) and set values passed through;
  - post-load validation: an unknown voice or language → `ConfigError` and the fake closed; matching is case-insensitive and the canonical lowercase value is sent;
  - library rejections: `ValueError` from `from_pretrained` → `ConfigError`; another exception → `TTSError`;
  - `synthesize_text`: float chunks (including out-of-range values) become whole little-endian int16 samples, clipped; `stream` gets the sentence, the language, the speaker and only the set options (no `instruct`/`temperature` by default; VoiceDesign sends `instruct` and no `speaker`); a multi-sentence text makes one `stream` call per sentence; every generator is closed;
  - a rate other than 24000 → `TTSError`;
  - an error while iterating → `TTSError` and the generator closed; a callback error propagates unchanged and the generator is closed, and survives a failing close;
  - cancellation while a packet is pending closes the generator before `CancelledError` surfaces (event-driven, no sleeps), for both input shapes;
  - `synthesize_text_stream`: one `stream()` call per sentence, and audio reaches the callback before the source finishes.
- **Lazy imports (`tests/modules/test_lazy_imports.py`)** — `qwentts_cpp` joins the poisoned libraries, `qwen3` joins the expected registry keys, and constructing `{"type": "qwen3"}` must raise the `tts-engine[qwen3]` hint.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row, `pytest.param("qwen3", {"language": "english"}, id="qwen3")`, plus `_REQUIRED_IMPORT["qwen3"] = "qwentts_cpp"`. It runs on an Apple-silicon Mac (first run downloads ≈1.26 GB) and **skips in CI**, where the extra installs nothing on the Linux runner. Robust properties only (PCM produced, drained once), never audio content.

## Validated against `qwentts-cpp-python==0.5.0`

Confirmed on macOS 26 arm64 (Apple M5 Pro), from the wheel's source and by running it (native revision `6fae929`, 2026-09-28):

- `QwenTTS.from_pretrained(model_id, *, quant="BF16", cache_dir=None, local_files_only=False, library_path=None, use_fa=True, clamp_fp16=False, codec_chunk_sec=24.0, log_level=None)`; known ids: the 0.6B/1.7B Base and CustomVoice models and 1.7B VoiceDesign; quants `F32`/`FP32`, `BF16`, `Q8`/`Q8_0`, `Q4`/`Q4_K_M`. An unknown id or quant raises `ValueError` before any download.
- `stream(*, text, lang="english", instruct=None, speaker=None, ..., temperature=0.9, top_k=50, top_p=1.0, repetition_penalty=1.05, max_new_tokens=2048, codec_chunk_sec=0.64, first_chunk_frames=4)` yields `(float32 1-D, 24000)`; chunk sizes 7680 then 15360 samples, plus a shorter last one.
- `model_type()` → `custom_voice` / `voice_design`; `speaker_names()` on CustomVoice → `serena, vivian, uncle_fu, ryan, aiden, ono_anna, sohee, eric, dylan` (empty on VoiceDesign); `language_names()` → the ten languages plus `beijing_dialect`, `sichuan_dialect`. Speaker and language matching are case-insensitive natively.
- Errors, all `QwenTTSError` on the first `next`: unknown speaker ("status -3"), unknown language, empty text, CustomVoice without speaker, VoiceDesign without instruct, a speaker on VoiceDesign.
- Closing the generator mid-synthesis returned in 3.6 ms; a `QwenTTS` built on one thread streams correctly from another.
- Timings in "Defaults, and why"; the first synthesis after load is slower (~0.3–1.2 s to first audio) while Metal pipelines compile.

Not verified: the Linux CUDA wheel (no GPU machine), and whether it loads at all without the CUDA runtime.

## Voice listing

`list_voices(config)` returns the preset speakers of the configured CustomVoice model (`model` and `quant`, defaulted as for construction): the model is loaded through `QwenTTS.from_pretrained`, its `speaker_names()` read, and the model closed again. Listing therefore downloads and loads the weights; it is the one backend whose voices exist only inside the model.

- **Selector:** `{"voice": <name>}`.
- **Metadata:** none: the model gives only names.
- **Model kinds:** a VoiceDesign model raises `VoiceListingUnsupported` before loading anything, since its voice is the `instruct` text. A `-Base` model is a `ConfigError`, as for construction.

## Open questions

Deferrals only (design is settled):

1. **Voice cloning (`-Base` models).** `stream()` takes `ref_audio_24k` + `ref_text`, or cached `ref_spk_emb`/`ref_codes`. Supporting it means reading and resampling a reference file (a WAV reader, or `soundfile`) and caching the extracted voice reference; add `ref_audio`/`ref_text` fields then.
2. **Linux and Windows coverage.** Revisit when `qwentts-cpp-python` publishes CPU or Windows wheels on PyPI; the extra's marker then widens, and the live row may run in CI if a CPU build is fast enough.
3. **More sampling knobs.** `top_k`, `top_p`, `repetition_penalty`, `seed`, greedy decoding (`do_sample=False`), `max_new_tokens`, and the packet sizes (`first_chunk_frames`, `codec_chunk_sec`) are plain keywords; each is a config field plus a validation rule.
4. **Native logs into `logging`.** `set_log_callback` could forward qwentts.cpp messages to the module logger, but it is process-wide and GGML logs bypass it; v1 only filters them to warnings.
5. **Local GGUF files.** `QwenTTS(talker_path, codec_path)` loads a GGUF pair from disk; a `talker`/`codec` field pair resolved with `resolve_path` would allow offline or custom quantizations.
