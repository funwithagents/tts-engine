---
code:
  - src/tts_engine/modules/piper/__init__.py
  - src/tts_engine/modules/piper/module.py
  - src/tts_engine/modules/base.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_piper.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Piper Module

**Status:** Implemented

## Overview

`piper` implements `TTSModule` with [Piper](https://github.com/OHF-Voice/piper1-gpl), the small, fast VITS-based TTS from the Open Home Foundation (the Home Assistant project), run in process through the `piper-tts` package: onnxruntime for the voice model and espeak-ng, compiled into the package, for phonemes. Its voices are small single-file ONNX models (16–110 MB), 178 of them in [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) covering 58 locales. It is the fastest local backend here (~50× real time on an Apple M5 Pro CPU, real time on a Raspberry Pi) and has the widest language coverage, at a lower naturalness than Kokoro.

It is a **local-model** backend following [tts-module-interface.md](../tts-module-interface.md) ("Local-model modules"), with the same shape as [kokoro.md](kokoro.md): Piper produces a whole sentence of audio per step, so the module synthesizes one sentence ahead of playback and feeds 100 ms slices, through the shared `feed_with_lookahead` helper.

### License note

`piper-tts` itself is **GPL-3.0-or-later** (the project moved from `rhasspy/piper`, MIT, to `OHF-Voice/piper1-gpl`), and the espeak-ng compiled into it is GPL-3.0. As for kokoro's G2P dependencies ([kokoro.md](kokoro.md), "License note"), this does not change the license of this repository: the extra only names the package, and the user installs it. Someone redistributing an application that bundles the `piper` extra is bound by the GPL; running it, locally or on a server, is not affected. The candidate analysis first rejected Piper for its license; it was moved to the integration list on 2026-10-26 on this reasoning.

**Voice licenses are a separate question, and they vary per voice.** Each voice is trained on a dataset whose license its `MODEL_CARD` names. Checked on 2026-10-26:

| Voice | Dataset license |
|---|---|
| `en_US-ljspeech-*` (the module default) | public domain (LJ Speech) |
| `en_US-bryce-medium`, `en_US-john-medium`, `en_US-kristin-medium`, `en_US-norman-medium` | public domain (the card or LibriVox) |
| `en_US-libritts_r-medium`, `en_GB-alba-medium`, `fr_FR-siwis-medium` | CC BY 4.0 (attribution required) |
| `en_US-lessac-*` (the voice most tutorials use) | Lessac/Blizzard 2013 **Research Licence Agreement** |
| `en_US-amy-*` | "See URL" (no stated terms) |

The default voice is a public-domain one for that reason, and the module's user page ([docs/modules/piper.md](../../docs/modules/piper.md)) tells users to read a voice's model card before shipping it.

## Config fields

All fields go under the `engine.module` block alongside `"type": "piper"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `voice` | non-empty string | no | `"en_US-ljspeech-medium"` | A voice key from `rhasspy/piper-voices`: `<locale>-<name>-<quality>`, e.g. `en_GB-alba-medium`, `fr_FR-siwis-medium`, `de_DE-thorsten-high`. Mutually exclusive with `model`. |
| `model` | non-empty string | no | — | A voice model outside the catalog: `hf://owner/repo/path/to/voice.onnx[@revision]`, or a local `.onnx` path resolved with `resolve_path`. Its config is the file of the same name plus `.json` (Piper's own convention), fetched or resolved the same way. Mutually exclusive with `voice`. |
| `speaker` | non-empty string or integer ≥ 0 | no | — (the voice's default speaker) | For a multi-speaker voice (`en_US-libritts_r-medium` has 2306, `en_US-arctic-medium` 18): a speaker name from the voice's `speaker_id_map`, or a speaker index below its `num_speakers`. Rejected for a single-speaker voice. |
| `speed` | number in [0.5, 2.0] | no | — (the voice's own pace) | Speaking rate multiplier. Piper's control is `length_scale` (phoneme duration, > 1 is slower), so the module passes `length_scale = voice.config.length_scale / speed`, keeping the voice's tuned pace at 1.0. Same field and range as kokoro. |

There is no API key and no device field (see "Device"). Unset fields are omitted from `SynthesisConfig`, so the voice's own defaults apply.

### Voice files

`piper-tts` loads a local `.onnx` and `.onnx.json` pair; its own `download_voices` helper writes to a directory of the caller's choosing. The module uses `huggingface_hub.hf_hub_download` instead, as kokoro does, so the voices land in the standard Hugging Face cache that CI already caches.

A `voice` key maps to its files by the repository's layout, which holds for all 178 voices in the catalog: `en_US-ljspeech-medium` → `en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx` (+ `.json`), where the first path segment is the locale's language part. Downloads are **pinned to commit `5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`** of `rhasspy/piper-voices` (2026-10-20), so a change to the repository cannot change what a configured voice loads; a voice added after that commit is reachable as `model: "hf://rhasspy/piper-voices/<path>.onnx@main"`.

The default voice, `en_US-ljspeech-medium`, is 63 MB, single-speaker, 22.05 kHz, public domain. The `high` variant (110 MB) is about four times slower for little audible gain on a laptop, and `lessac` is excluded by its license.

## Validation

At construction, before any download:

- `voice`, `model` — if present, non-empty strings, and not both. A `voice` must have the `<locale>-<name>-<quality>` shape (three non-empty parts separated by `-`); an `hf://` `model` must have an owner, a repository and a file path, as in kokoro.
- `speaker` — if present, a non-empty string or an integer ≥ 0 (booleans rejected).
- `speed` — if present, a number in [0.5, 2.0] (booleans rejected).

After the lazy import, before any download: a local `model` path, or its `.json` beside it, that is not an existing file → `ConfigError` naming the path. (`PiperVoice.load` opens the JSON first and raises `FileNotFoundError` for it, but a missing `.onnx` surfaces as onnxruntime's `NoSuchFile`.)

After loading, against the loaded voice:

- **Phoneme type.** `pinyin` (the newer Chinese voices, `zh_CN-chaowen-medium` and `zh_CN-xiao_ya-medium`) → `ConfigError`: its phonemizer needs `piper-tts`'s `zh` extra (transformers) and downloads a g2pW model into a working directory, which v1 does not support. Every other type is probed: the module phonemizes one word (`voice.phonemize("a")`). An `ImportError` there → `ConfigError` naming the missing package: the Japanese and Thai voices need `piper-tts`'s `ja` / `th` extras (`pyopenjtalk`, `tltk`), which the `piper` extra does not install. `espeak` (almost every voice, including the `zh_CN-huayan-*` Chinese voices), `text` and `hebrew` (a model shipped in the wheel) need nothing more. The probe also initializes espeak-ng before the first sentence.
- **Speaker.** A name must be in `config.speaker_id_map`, an index below `config.num_speakers`, else `ConfigError` (the number of speakers and a few names). Any `speaker` for a voice with one speaker → `ConfigError`. Without this check an out-of-range index only fails at the first synthesis, inside onnxruntime.

## Implementation notes

### Dependencies and lazy import

Requires the `piper` extra: `pip install tts-engine[piper]` / `uv sync --extra piper`, declared as

```toml
piper = [
    "piper-tts>=1.8,<2",
    "huggingface-hub",
]
```

and added to `all-modules`. `piper-tts` 1.8.0 (2026-09) depends on `onnxruntime` and `pathvalidate` only and ships abi3 wheels for Linux x86_64/aarch64, macOS arm64/x86_64 and Windows x86_64, with espeak-ng and its data inside. The ceiling is because the module is validated against 1.8.0 only. Like kokoro, the extra does not install on Intel macOS: onnxruntime has no wheel there, even though `piper-tts` does.

`piper/module.py` must not import `piper` or `huggingface_hub` at file top. Both imports happen inside `__init__`:

```python
try:
    from huggingface_hub import hf_hub_download
    from piper import PiperVoice, SynthesisConfig
except ImportError as exc:
    raise ConfigError(
        "The 'piper' module requires the piper extra: pip install tts-engine[piper]"
    ) from exc
```

The package's module is named `piper`, the same as this repository's `tts_engine.modules.piper` package. There is no clash: the latter is only ever imported by its full dotted name, and absolute imports resolve `piper` to the installed library.

### Construction (eager model load)

After validation and the lazy import:

1. Resolve the `.onnx` and `.onnx.json` to local paths: `hf_hub_download` for a catalog voice or an `hf://` `model` (both files from the same repository and revision), `resolve_path` for a local `model`.
2. `voice = PiperVoice.load(onnx_path, config_path=json_path)`: ~0.7–1.0 s.
3. Post-load checks (above), then build the `SynthesisConfig` from the set fields only: `speaker_id` (an index, or the name looked up in `speaker_id_map`), `length_scale` (from `speed`).

One `log.info` after the load names the voice (or model), its sample rate and the speaker count.

`sample_rate` returns `voice.config.sample_rate`: **22050** for `medium`/`high` voices, **16000** for `low`/`x_low` ones. Every chunk carries its rate; the module raises `TTSError` if one ever differs from the declared rate rather than playing at the wrong speed.

### Device

`PiperVoice.load(use_cuda=False)` runs on `CPUExecutionProvider`, which is the right default: the default voice synthesizes 7 s of audio in ~0.14 s on an Apple M5 Pro. `use_cuda=True` needs `onnxruntime-gpu`; not exposed in v1 (see "Open questions").

### Synthesis

`voice.synthesize(text, syn_config)` is a generator: it phonemizes the text with espeak-ng, which splits it into sentences, and yields one `AudioChunk` per sentence as it is synthesized. A chunk's `audio_int16_bytes` is the sentence's audio already converted to little-endian int16 (clipped and scaled by the library, after its default peak normalization), so the module feeds those bytes unchanged.

Both input shapes go through `segment_sentences` first: `synthesize_text` over `segment_sentences([text])`, `synthesize_text_stream` over `segment_sentences(pieces)` from `run_text_stream_worker`. Each segment is passed to `synthesize`, which may still split it further (a segment cut at a clause by the length cap stays one call). Segmenting the complete text too keeps one code path for both shapes, and cancellation takes effect between segments.

### Lookahead and feeding

As for kokoro, a sentence's audio arrives all at once and the default sink's `feed` blocks for its duration, so synthesizing a sentence only after the previous one has played would leave a gap. The worker hands the segments to `feed_with_lookahead` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers"), with a `synthesize` function that runs `voice.synthesize(segment, syn_config)` and yields each chunk's int16 bytes, wrapping library errors in `TTSError`. The helper's producer thread stays at most one chunk ahead of playback, and the PCM is fed in 100 ms slices (2 × `sample_rate // 10` bytes), checking `stop` between slices.

At ~50× real time the gap would be short with Piper (tens of milliseconds per sentence), but nonzero on a slow CPU such as a Raspberry Pi, and sharing the kokoro path costs nothing.

### Cancellation

As for kokoro: `stop` is polled between slices and while waiting for the next chunk, so no callback happens after the coroutine has returned, which it does within ~100 ms of a cancel. A sentence's inference already running finishes in the background thread (it is short) and is discarded.

### Error handling

At construction:

- A missing extra → `ConfigError` with the install hint.
- A field failing the module's own validation (before or after load) → `ConfigError`.
- A missing Hub file, repository or revision (`RemoteEntryNotFoundError`, `RepositoryNotFoundError`, `RevisionNotFoundError`), e.g. a `voice` key that is not in the catalog → `ConfigError(f"Piper module: {exc}")`, chained. Other download failures (offline with nothing cached, network errors) → `TTSError(f"piper model load failed: {exc}")`, chained. Same split as kokoro.
- Any exception from `PiperVoice.load` → `TTSError(f"piper model load failed: {exc}")`, chained: missing local files were caught before, so what remains is a corrupt or incompatible voice.

At synthesis:

- Any exception while advancing `synthesize` → `TTSError(f"piper synthesis failed: {exc}")`, chained.
- A chunk at an unexpected rate → `TTSError`.
- Exceptions raised by `callback` propagate unchanged; text-stream failures propagate unchanged through `run_text_stream_worker`.

A segment that phonemizes to nothing (`"..."`) yields no chunk: no audio, no error.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` drops the `PiperVoice` so its ONNX session can be collected; the module is not usable afterwards.

## Module ID

Registered in `REGISTRY` as `"piper"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)); class `PiperModule` in `modules/piper/module.py`, re-exported from `modules/piper/__init__.py`.

## Example config

`examples/config.piper.json`:

```json
{
  "engine": {
    "module": {
      "type": "piper",
      "voice": "en_US-ljspeech-medium"
    },
    "player": { "device": null }
  },
  "server": { "host": "127.0.0.1", "port": 8000 }
}
```

## Testing

- **Unit (`tests/modules/test_piper.py`)** — `piper` and `huggingface_hub` are **faked** (stub modules injected through `monkeypatch.setitem(sys.modules, ...)`), so the fast tier never downloads or loads a voice. The fake `PiperVoice.load` records its paths and returns a voice whose `config` has a sample rate, speakers, a `length_scale` and a phoneme type; its `phonemize` can raise; its `synthesize` records the text and `SynthesisConfig` and yields fake chunks (`sample_rate`, `audio_int16_bytes`), or raises. Covers:
  - `ConfigError` with the install hint when `piper` is unimportable;
  - validation before load: empty or non-string fields, `voice` with `model`, a malformed voice key or `hf://` value, a bad `speaker` or `speed`; none reaches the fake download;
  - voice files: the default voice downloads `en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx` and `.onnx.json` at the pinned revision; another key maps to its path; an `hf://` `model` fetches the `.onnx` and its `.json`; a local `model` is resolved against `base_dir` and not downloaded; a missing local `.onnx` or `.json` → `ConfigError` before load;
  - download and load failures: a missing Hub entry, repository or revision → `ConfigError`; a cache miss or another download error → `TTSError`; a load error → `TTSError`;
  - phoneme types: `pinyin` → `ConfigError`; a phonemizer `ImportError` → `ConfigError`; `espeak` passes;
  - speaker: a name mapped to its index, an index in range passed through, an unknown name or out-of-range index → `ConfigError`, any speaker on a single-speaker voice → `ConfigError`;
  - `sample_rate` is the voice's rate (a 16 kHz voice reports 16000);
  - `synthesize_text`: one `synthesize` call per sentence; the `SynthesisConfig` carries only the set fields (`length_scale` = voice default / `speed`); the chunks' bytes reach the callback unchanged, in slices of at most 100 ms; a chunk at another rate → `TTSError`; a library error → `TTSError`; a callback error propagates unchanged;
  - cancellation while feeding stops callbacks before `CancelledError` surfaces, for both input shapes;
  - `synthesize_text_stream`: one `synthesize` call per sentence, and audio before the source finishes.
- **Shared helper (`tests/modules/test_tts_module_interface.py`)** — `feed_with_lookahead` gets its own tests (see the interface spec); the kokoro tests keep covering kokoro's use of it.
- **Lazy imports (`tests/modules/test_lazy_imports.py`)** — `piper` joins the poisoned libraries, `piper` the expected registry keys, and constructing `{"type": "piper"}` must raise the `tts-engine[piper]` hint.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row, `pytest.param("piper", {}, id="piper")`, plus `_REQUIRED_IMPORT["piper"] = "piper"`. No key, the extra comes with the `dev` group: the row runs in CI on Linux and on the Mac (first run downloads 63 MB). Robust properties only.

## Validated against `piper-tts==1.8.0`

Confirmed on macOS 26 arm64 (Apple M5 Pro), Python 3.13, `onnxruntime` 1.31.0, by running it:

- `PiperVoice.load(model_path, config_path=None, use_cuda=False, espeak_data_dir=<bundled>, download_dir=None, include_alignments=False)`; `config_path` defaults to `model_path + ".json"`. A missing JSON raises `FileNotFoundError`; with the JSON present, a missing `.onnx` raises onnxruntime's `NoSuchFile`.
- `PiperConfig` exposes `sample_rate`, `num_speakers`, `speaker_id_map`, `phoneme_type` (`espeak`, `text`, `pinyin`, `hebrew`, `japanese`, `thai`), `espeak_voice`, `length_scale`, `default_speaker_id`.
- `synthesize(text, syn_config=None, include_alignments=False)` yields one `AudioChunk` per sentence (`sample_rate`, `audio_float_array` float32, `audio_int16_bytes`); three sentences → three chunks, the first after ~26 ms. `SynthesisConfig(speaker_id, length_scale, noise_scale, noise_w_scale, normalize_audio=True, volume=1.0)`.
- `en_US-ljspeech-medium`: 22050 Hz, one speaker, load ~1.0 s, 7.0 s of audio in 0.14 s warm. `-high`: load 0.7 s, 6.5 s of audio in 0.70 s. Peak 1.0 (normalized).
- Empty text and `"..."` yield no chunk, no error. A `speaker_id` on a single-speaker voice is ignored; an out-of-range one on `en_US-arctic-medium` (18 speakers) raises onnxruntime `InvalidArgument` at synthesis.
- `he_IL-saspeech-medium` phonemizes with no extra; `ja_JP-hi_fi_captain-medium` and `th_TH-tsync2-medium` raise `ModuleNotFoundError` (`pyopenjtalk`, `tltk`) on `phonemize`.
- A voice loaded on one thread synthesizes from another.
- Through the module: `zh_CN-huayan-*` load (espeak, `cmn`), `zh_CN-chaowen-medium` and `zh_CN-xiao_ya-medium` are rejected as `pinyin`; an unknown voice key (`en_US-nope-medium`) is a `ConfigError` from the Hub's 404; against a real-time blocking sink, first audio after 65 ms, no wait between sentence feeds, cancellation returning in 8 ms.
- The `voice` key → path rule (`<lang>/<locale>/<name>/<quality>/<key>.onnx`) holds for all 178 entries of `voices.json` at the pinned commit.

## Voice listing

`list_voices(config)` returns the catalog's `voices.json`, downloaded from `rhasspy/piper-voices` at the pinned commit like the voices themselves. It reads no config field. The catalog has no gender or age field.

- **Selector:** a single-speaker voice is one entry, `{"voice": <key>}`. A multi-speaker voice is one entry per speaker, in speaker-id order, with id `<key>/<speaker>` and `{"voice": <key>, "speaker": <speaker name>}`. That makes about 4100 entries, 2306 of them `en_US-libritts_r-medium`'s numbered speakers.
- **Metadata:** the language code (`en_US`); the language and country in English as the description; the quality (`low`, `medium`, `high`) as a tag.
- **Errors:** a failed download is a `TTSError`.

## Open questions

Deferrals only (design is settled):

1. **Pinyin Chinese, Japanese and Thai voices.** They need `piper-tts`'s `zh`/`ja`/`th` extras, and Chinese a g2pW download directory. Add matching extras (or document a manual install) and pass `download_dir` (a cache directory) if users want them.
2. **GPU.** `use_cuda=True` with `onnxruntime-gpu`; Piper is fast enough on CPU that this matters only for many concurrent engines.
3. **Synthesis knobs.** `noise_scale`, `noise_w_scale`, `volume` and `normalize_audio` are one omitted-when-unset `SynthesisConfig` field each.
4. **Phoneme alignments.** `include_alignments=True` gives per-phoneme timings (lip sync, word highlighting); the engine has no channel for them yet.
