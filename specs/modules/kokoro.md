---
code:
  - src/tts_engine/modules/kokoro/__init__.py
  - src/tts_engine/modules/kokoro/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_kokoro.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Kokoro Module

**Status:** Implemented

## Overview

`kokoro` implements `TTSModule` with [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0, 82M parameters, 54 voices across American and British English, Spanish, French, Hindi, Italian, Japanese, Brazilian Portuguese and Mandarin), run in process through [`kokoro-onnx`](https://github.com/thewh1teagle/kokoro-onnx) (MIT): onnxruntime for the model and a bundled espeak-ng for grapheme-to-phoneme conversion. It is a **local-model** backend following [tts-module-interface.md](../tts-module-interface.md) ("Local-model modules"), the same shape as [qwen3.md](qwen3.md): the library is wrapped directly, inference runs off the event loop, float waveforms become signed-16-bit PCM before the callback, and text is synthesized sentence by sentence. Unlike pocket, phonon and qwen3, the runtime produces a **whole sentence** of audio per call, so the module synthesizes one sentence ahead while the previous one plays (see "Lookahead").

### Why `kokoro-onnx` and not the official `kokoro` package

The candidate analysis named `pip install kokoro` (hexgrad's torch package, `KPipeline`). Checked on 2026-10-26, it does not fit this project:

- **Python.** `kokoro` 0.9.4 (the latest, April 2025) and its G2P package `misaki` 0.9.4 both declare `requires-python <3.13`. Local development here runs Python 3.13 (`.python-version`), and the `dev` group installs every extra, so the official package could never be installed or tested on the development machine.
- **Weight.** It pulls in `torch`, `transformers`, `spacy` and `spacy-curated-transformers`, and `misaki[en]` downloads a spaCy model at first use.

`kokoro-onnx` 0.6.1 (2026-08) runs the same Kokoro-82M v1.0 weights exported to ONNX. It depends on `onnxruntime`, `numpy`, `phonemizer` and `espeakng-loader` (espeak-ng shipped as a wheel for Linux x86_64/aarch64, macOS arm64/x86_64 and Windows, so no system package is needed), and supports Python 3.10–3.13. Pipecat and fastrtc both use it for Kokoro. The cost is G2P quality: the official package uses misaki's dictionary and neural G2P for English, while `kokoro-onnx` uses espeak-ng directly. misaki can be added later as an option (see "Open questions").

### License note

The model (Apache-2.0), `kokoro-onnx` (MIT) and onnxruntime (MIT) are permissive. The G2P path is not: `phonemizer` is GPLv3+ and the espeak-ng library bundled by `espeakng-loader` is GPL-3.0. The official `kokoro` package has the same dependency (misaki falls back to espeak-ng through `phonemizer-fork`), so the choice of runtime does not avoid it. Like every provider, the extra is opt-in and nothing in the base install depends on it. Anyone redistributing an application that bundles the `kokoro` extra should check what the GPL requires of them.

## Config fields

All fields go under the `engine.module` block alongside `"type": "kokoro"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `voice` | non-empty string | no | `"af_heart"` | A voice name from the voices file, e.g. `af_heart`, `am_michael`, `bf_emma`, `ff_siwis`. The first letter of the name is its language (`a` American English, `b` British English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `j` Japanese, `p` Brazilian Portuguese, `z` Mandarin), the second its gender. Checked against the loaded voices file. |
| `language` | non-empty string | no | from the voice's first letter (see below) | An espeak-ng language code passed to the phonemizer: `en-us`, `en-gb`, `es`, `fr-fr`, `hi`, `it`, `ja`, `pt-br`, `cmn`, or any other code espeak-ng supports. Checked at load. |
| `speed` | number in [0.5, 2.0] | no | — (library default 1.0) | Speaking rate multiplier. |
| `model` | non-empty string | no | the default model (see "Model files") | The ONNX model: `hf://owner/repo/path/to/file.onnx[@revision]`, or a local path resolved with `resolve_path`. |
| `voices` | non-empty string | no | the default voices file (see "Model files") | The voices file (a NumPy `.npz` archive of style vectors, as published by kokoro-onnx): `hf://owner/repo/path/to/file.bin[@revision]`, or a local path resolved with `resolve_path`. |

There is no API key, and no device field: onnxruntime runs on the CPU (see "Device").

**Default language.** Voice names encode their language, so the module derives `language` from the voice's first letter when it is not set: `a` → `en-us`, `b` → `en-gb`, `e` → `es`, `f` → `fr-fr`, `h` → `hi`, `i` → `it`, `j` → `ja`, `p` → `pt-br`, `z` → `cmn`. This is the table `kokoro-onnx`'s examples use and the one the official package applies (`lang_code` = the voice's first letter). A voice whose first letter is not in the table (a custom voices file) needs an explicit `language`, else `ConfigError`. Setting `language` explicitly always wins, e.g. an American voice reading British-English phonemes.

### Model files

`kokoro-onnx` does not download anything: its constructor takes two local paths. The module fetches them from the Hugging Face Hub with `huggingface_hub.hf_hub_download`, into the standard Hugging Face cache that the other local backends (and CI's cache step) already use.

The defaults are `kokoro-v1.0.onnx` (325 MB, fp32) and `voices-v1.0.bin` (28 MB) from [`fastrtc/kokoro-onnx`](https://huggingface.co/fastrtc/kokoro-onnx), **pinned to commit `8d07950c9b6c87ce6809e9bba7bd494336217c2a`**:

- These are the files kokoro-onnx itself publishes as GitHub release assets, mirrored on the Hub by the fastrtc project. Checked on 2026-10-26: `voices-v1.0.bin` is byte-identical to the asset in the `model-files-v1.1` release (same SHA-256), and `kokoro-v1.0.onnx` has the byte size of the `model-files-v1.0` release's export (that release published no digests). The later `model-files-v1.1` re-export is only on GitHub.
- The canonical Hub conversion, [`onnx-community/Kokoro-82M-v1.0-ONNX`](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX), ships one raw file per voice instead of the `.npz` archive `kokoro-onnx` loads, so it cannot be passed as-is.
- Pinning the revision means a change to the mirror repository cannot change what the module loads.

An `hf://` value is parsed as `hf://<owner>/<repo>/<path inside the repo>[@<revision>]`: the first two segments are the repository, the rest is the file, and an optional `@revision` suffix selects a branch, tag or commit (default `main`). This is the `hf://` convention of the pocket and phonon modules, with a file path added. A local path is the escape hatch for the GitHub release assets (`kokoro-v1.0.int8.onnx`, `kokoro-v1.0.fp16.onnx`, the v1.1 re-export) or any other compatible export.

## Validation

At construction, before any download:

- `voice`, `language`, `model`, `voices` — if present, non-empty strings. An `hf://` value must have an owner, a repository and a file path, else `ConfigError`.
- `speed` — if present, a number in [0.5, 2.0], booleans rejected.
- No `language`, and a voice whose first letter is not in the default-language table → `ConfigError` asking for `language`.

After the lazy import, before any download:

- A local `model` or `voices` path that is not an existing file → `ConfigError` naming the field and the resolved path. The module checks this itself because `kokoro-onnx` opens the ONNX session *before* its own file check, so a missing model would otherwise surface as onnxruntime's `NoSuchFile` error.

After loading, against the loaded model:

- `voice` must be in `kokoro.get_voices()`, else `ConfigError` naming the voice and the number of voices available (the full list is 54 names, too long for an error message).
- `language` is checked by phonemizing one word through the loaded tokenizer (`kokoro.tokenizer.phonemize("a", language)`). espeak-ng rejects an unknown code with `RuntimeError` ("language … is not supported by the espeak backend"), which the library would otherwise only raise at the first synthesis; the module turns it into `ConfigError`. The call also pays espeak-ng's one-time initialization (~0.6 s) at load rather than on the first sentence.

`tokenizer` is a public attribute of `Kokoro` but not documented API; the pin below 0.7 covers it, and the unit tests fake it explicitly so a change shows up in the live row.

## Implementation notes

### Dependencies and lazy import

Requires the `kokoro` extra: `pip install tts-engine[kokoro]` / `uv sync --extra kokoro`, declared as

```toml
kokoro = [
    "kokoro-onnx>=0.6.1,<0.7",
    "huggingface-hub",
]
```

and added to `all`. The ceiling is because the package is pre-1.0 and the module is validated against 0.6.1 only. `huggingface-hub` is listed because the module imports it directly; `kokoro-onnx` does not depend on it.

**Platforms.** `onnxruntime` 1.31 publishes wheels for Linux x86_64/aarch64, macOS arm64 (14+) and Windows x86_64/arm64, for Python 3.11–3.14. It has **no Intel-macOS wheel**, so like `pocket` the extra does not install on an Intel Mac (this settles an item from the candidate analysis's "Unverified" list). `kokoro-onnx` declares `requires-python <3.14`. uv ignores upper bounds on a dependency's `requires-python` when resolving, so the lock still resolves for `>=3.11`; on Python 3.14 the extra would fail to install until kokoro-onnx lifts its cap.

`kokoro/module.py` must not import `kokoro_onnx` or `huggingface_hub` at file top (the registry imports every module class). Both imports happen inside `__init__`:

```python
try:
    from huggingface_hub import hf_hub_download
    from kokoro_onnx import Kokoro
except ImportError as exc:
    raise ConfigError(
        "The 'kokoro' module requires the kokoro extra: pip install tts-engine[kokoro]"
    ) from exc
```

### Construction (eager model load)

The engine reads `sample_rate` once at construction, so the model is loaded in `__init__`, after validation and the lazy import:

1. Resolve `model` and `voices` to local paths: `hf_hub_download(repo_id, filename, revision=...)` for an `hf://` value (a no-op when the file is already cached), `resolve_path` for anything else.
2. `kokoro = Kokoro(model_path, voices_path)`. Loading takes ~0.7–0.8 s on an Apple M5 Pro CPU, on top of the first download (~353 MB).
3. Post-load checks (above).

One `log.info` after the load names the model, the voice, the language and the onnxruntime providers (`kokoro.sess.get_providers()`); nothing is logged per sentence.

`sample_rate` returns **24000**, `kokoro_onnx.config.SAMPLE_RATE`, the model's only rate. `create` returns `(audio, 24000)`, and the module raises `TTSError` if the returned rate ever differs rather than playing at the wrong speed.

### Device

`kokoro-onnx` builds its onnxruntime session on `CPUExecutionProvider` unless an accelerated onnxruntime distribution (`onnxruntime-gpu`, `-directml`, …) is installed, in which case it uses every available provider. The `ONNX_PROVIDER` environment variable overrides both. The module adds no device field. On the development Mac, CoreML (available in the stock wheel) loaded in 4.2 s instead of 0.8 s and synthesized no faster than the CPU, so CPU is the right default there. A Linux NVIDIA host can install `onnxruntime-gpu` next to the extra; this is documented, not verified (see "Open questions").

### Synthesis

`kokoro.create(text, voice=..., lang=..., speed=...)` phonemizes the text, splits the phonemes into batches of at most 510, runs the model on each batch, trims the silence at the batch edges, and returns one float32 1-D array at 24 kHz. Unset fields are omitted from the call, so the library's own `speed` default applies. Measured on an Apple M5 Pro CPU, warm: ~0.4 s for a short sentence (1.7 s of audio), ~1.3 s for a 150-character one (6.9 s of audio), about 4–5× real time. The first sentence after load is not slower once espeak-ng has been initialized at load.

`_to_int16_pcm` is the phonon conversion: flatten, clip to [-1, 1], scale by 32767, little-endian int16. Peak amplitude measured 0.38–0.51, so clipping is a guard, not a gain stage.

**Feeding in slices.** A sentence's audio arrives all at once, and the default sink's `feed` blocks for the duration of the chunk it is given. The module therefore feeds each sentence in 100 ms slices (2400 samples), checking `stop` between slices, as the mistral and openai modules do, so a cancellation takes effect within 100 ms of playback.

### Both input shapes: sentence grouping

`kokoro-onnx` has no incremental text input. Both methods turn their input into sentences with `segment_sentences` and synthesize one `create` call per sentence: `synthesize_text` over `segment_sentences([text])`, `synthesize_text_stream` over `segment_sentences(pieces)` from `run_text_stream_worker`. The complete-text path segments too (as qwen3 does) because `create` returns nothing until the whole text is synthesized: a paragraph would otherwise take several seconds before its first sample.

### Lookahead

Synthesizing a sentence only after the previous one has finished playing would leave a silence of one sentence's inference time (0.2–1.3 s measured) between sentences. The module overlaps them, as `kokoro-onnx`'s own `create_stream` does, through the shared `feed_with_lookahead` helper ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers"): the worker passes it the segments and a `synthesize` function returning `[self._create(sentence)]` (one int16 chunk per sentence, `create` failures already wrapped in `TTSError`), with 100 ms slices. The helper's producer thread synthesizes at most one sentence ahead of playback and is not joined on cancellation, because onnxruntime's `run` has no cancel. A new utterance started while that last call finishes runs concurrently with it, which onnxruntime sessions support (`kokoro-onnx` serializes the espeak-ng calls with its own lock).

### Cancellation

Cooperative, as for every module: `stop` is polled between slices and while waiting for the next sentence, so no callback happens after `stop` is set, and the coroutine returns within ~100 ms of a cancel, plus up to 50 ms of queue polling. Inference already running finishes in the background (above).

### Error handling

At construction:

- A missing extra → `ConfigError` with the install hint (above).
- A field failing the module's own validation (before or after load) → `ConfigError`.
- A Hub file, repository or revision that does not exist (`huggingface_hub.errors.RemoteEntryNotFoundError`, `RepositoryNotFoundError`, `RevisionNotFoundError`) → `ConfigError(f"Kokoro module: {exc}")`, chained: the configured reference is wrong. Not the broader `EntryNotFoundError`: its other subclass, `LocalEntryNotFoundError`, means "offline and not cached", a network condition.
- A missing local file is caught before the load (see "Validation"), so the module never has to tell a missing file apart from other failures of the `Kokoro` constructor. The module's own validation runs outside the download and load `try` blocks, since `ConfigError` is itself a `ValueError`.
- Any other exception while downloading or loading (a network failure, being offline with nothing cached, which is also a `FileNotFoundError`, a corrupt or incompatible ONNX file, a voices file that is not an `.npz` archive) → `TTSError(f"kokoro model load failed: {exc}")`, chained.

At synthesis:

- Any exception from `create` → `TTSError(f"kokoro synthesis failed: {exc}")`, chained. This includes `ValueError("Nothing to synthesize …")` for a segment that produces no phonemes. `segment_sentences` never yields a blank segment, and a punctuation-only one (`...`) was measured to produce a short silence rather than an error, so this is not expected in practice.
- Exceptions raised by `callback` propagate unchanged.
- Text-stream failures propagate unchanged through `run_text_stream_worker`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` drops the `Kokoro` runtime so its ONNX session can be collected; the module is not usable afterwards.

## Module ID

Registered in `REGISTRY` as `"kokoro"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)); class `KokoroModule` in `modules/kokoro/module.py`, re-exported from `modules/kokoro/__init__.py`.

## Example config

`examples/config.kokoro.json`:

```json
{
  "engine": {
    "module": {
      "type": "kokoro",
      "voice": "af_heart"
    },
    "player": { "device": null }
  },
  "server": { "host": "127.0.0.1", "port": 8000 }
}
```

## Testing

- **Unit (`tests/modules/test_kokoro.py`)** — `kokoro_onnx` and `huggingface_hub` are **faked** (stub modules injected through `monkeypatch.setitem(sys.modules, ...)`), so the fast tier never downloads or loads a model. The fake `hf_hub_download` records its arguments and returns a path; the fake `Kokoro` records its constructor paths, lists voices, has a `tokenizer.phonemize` that can reject a language, and a `create` that records its calls and returns given float32 arrays (or raises, or blocks on an event). Covers:
  - `ConfigError` with the install hint when `kokoro_onnx` is unimportable;
  - validation before load: empty or non-string fields, `speed` out of range or boolean, a malformed `hf://` value, an unknown voice prefix with no `language`; none of these reaches the fake download or constructor;
  - model files: the defaults download the pinned repository and revision; an `hf://owner/repo/dir/file@rev` value is split into repository, file and revision; a local path is resolved against `base_dir` and not downloaded; a local path that does not exist → `ConfigError` before the constructor runs;
  - language: derived from the voice prefix (`af_heart` → `en-us`, `ff_siwis` → `fr-fr`), an explicit `language` wins, and a language the phonemizer rejects → `ConfigError`;
  - an unknown voice after load → `ConfigError`;
  - load failures: a missing Hub entry, repository or revision → `ConfigError`; a local-cache miss (a `FileNotFoundError` subclass) or another download error → `TTSError`; an exception from the constructor → `TTSError`;
  - `synthesize_text`: one `create` call per sentence with the voice, the language and only the set options; float samples (including out-of-range values) become clipped little-endian int16; a long sentence is fed in slices of at most 2400 samples; a rate other than 24000 → `TTSError`;
  - an exception from `create` → `TTSError`; a callback error propagates unchanged;
  - lookahead: the second sentence's `create` runs while the first sentence's audio is still being fed (the callback waits on an event that the fake `create` sets for sentence two), event-driven, no sleeps;
  - cancellation while feeding stops callbacks before `CancelledError` surfaces, for both input shapes, and the producer starts no further `create` call;
  - `synthesize_text_stream`: one `create` per sentence, and audio reaches the callback before the source finishes.
- **Lazy imports (`tests/modules/test_lazy_imports.py`)** — `kokoro_onnx` joins the poisoned libraries, `kokoro` the expected registry keys, and constructing `{"type": "kokoro"}` must raise the `tts-engine[kokoro]` hint.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row, `pytest.param("kokoro", {}, id="kokoro")`, plus `_REQUIRED_IMPORT["kokoro"] = "kokoro_onnx"`. It needs no key and the extra comes with the `dev` group, so the row runs in CI on Linux as well as on the Mac (first run downloads ~353 MB into the Hugging Face cache). Robust properties only (PCM produced, drained once, cancellation stops audio), never audio content.

## Validated against `kokoro-onnx==0.6.1`

Confirmed on macOS 26 arm64 (Apple M5 Pro), Python 3.13, `onnxruntime` 1.31.0, `phonemizer` 3.4.0, `espeakng-loader` 0.2.4, by running it:

- `Kokoro(model_path, voices_path, espeak_config=None, vocab_config=None)`. It creates the onnxruntime session first, so a missing model file raises onnxruntime's `NoSuchFile` (not a `FileNotFoundError`); its own `FileNotFoundError` check runs only after the session exists, so in practice it covers the voices file alone. The session runs on `['CPUExecutionProvider']` with the stock wheel, although `CoreMLExecutionProvider` is available.
- `create(text, voice, speed=1.0, lang="en-us", is_phonemes=False, trim=True, sentence_pause=0.25, clause_pause=0.1, continuous=False) -> (float32 1-D ndarray, 24000)`. `create_stream` exists but is an async generator on the caller's event loop and the default executor, so the module does not use it.
- `get_voices()` on `voices-v1.0.bin` → 54 names with the prefixes `af am bf bm ef em ff hf hm if im jf jm pf pm zf zm`.
- `en-us`, `en-gb`, `fr-fr`, `es`, `it`, `pt-br`, `hi`, `cmn` and `ja` all synthesize with their matching voice (Japanese and Mandarin through espeak-ng, without misaki).
- Errors: unknown voice → `ValueError("Voice … not found in available voices")`; speed 3.0 → `ValueError("Speed should be between 0.5 and 2.0 …")`; an unknown `lang` → `RuntimeError('language "xx-yy" is not supported by the espeak backend')`, also from `tokenizer.phonemize`; empty text → `ValueError("Nothing to synthesize …")`; `"..."` → 0.3 s of audio, no error; 2,000 characters → one 110 s array, no error.
- A `Kokoro` built on one thread synthesizes correctly from another.
- `huggingface_hub` 1.30: a missing file raises `RemoteEntryNotFoundError`, an unknown repository `RepositoryNotFoundError` (HTTP 401 when unauthenticated), an unknown revision `RevisionNotFoundError`. `LocalEntryNotFoundError` subclasses both `EntryNotFoundError` and `FileNotFoundError`.
- Timings as in "Synthesis"; `tokenizer.phonemize` on a fresh instance took 0.58 s (espeak-ng initialization).

## Open questions

Deferrals only (design is settled):

1. **misaki G2P for English.** `kokoro-onnx` accepts phonemes (`is_phonemes=True`), and its examples pair it with `misaki-fork[en]` for better English pronunciation. That would bring spaCy back; add it as an opt-in field (`g2p: "misaki"`) behind its own extra if espeak-ng's English proves too weak.
2. **GPU providers.** `onnxruntime-gpu` on Linux x86_64 is picked up by `kokoro-onnx` automatically; it is not verified here (no NVIDIA machine). A `provider` field could replace the `ONNX_PROVIDER` environment variable if a user needs per-engine control.
3. **Quantized and v1.1 exports.** The int8 (114 MB) and fp16 (164 MB) exports and the v1.1 re-export are GitHub release assets, reachable today as local `model` paths. If a pinned Hub copy appears, it can become a documented `model` value.
4. **Voice blending.** `create` accepts a style vector instead of a name, so a weighted mix of voices (`"af_heart:0.7,af_bella:0.3"`) is a small parsing addition.
5. **Mandarin v1.1 (`kokoro-v1.1-zh.onnx` + `voices-v1.1-zh.bin`).** Works with `kokoro-onnx` (it adapts to the export's integer `speed` input) but needs its own vocabulary config; not covered by v1.
