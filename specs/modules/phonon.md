---
code:
  - src/tts_engine/modules/phonon/__init__.py
  - src/tts_engine/modules/phonon/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_phonon.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Phonon Module

**Status:** Implemented

## Overview

`phonon` implements `TTSModule` using [Phonon](https://gradium.ai/phonon), Gradium's open-weight **on-device** TTS family (released 2026-10-20), through its official runtime, the [`ptts`](https://pypi.org/project/ptts/) package ([gradium-ai/xn-ptts](https://github.com/gradium-ai/xn-ptts)). It is a **local-model** backend following [tts-module-interface.md](../tts-module-interface.md) ("Local-model modules"), the same shape as [pocket.md](pocket.md): the model library is wrapped directly, inference runs in the cancellable worker, float waveforms are converted to signed-16-bit PCM before the callback, and a text stream is grouped into sentences.

What sets it apart from `pocket` is the runtime: `ptts` is a **Rust extension with no PyTorch** (its only Python dependency is `numpy`), shipped as `abi3` wheels for every platform this project supports, Intel macOS included. The default checkpoint is small (31M parameters) and runs many times faster than real time on one CPU. So it is the lightest real-speech backend the project offers, and the only one besides the fixtures that installs and runs everywhere with no key.

It is unrelated to the `gradium` module: Phonon is not a model of the Gradium cloud API (whose `model_name` values are `default` and `gradium-tts-beta`), and this module makes no call to that API.

## Config fields

All fields go under the `engine.module` block alongside `"type": "phonon"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `language` | string | no | `"en"` | Text-normalization language, passed as `TTS(lang=...)`: one of `"en"`, `"fr"`, `"de"`, `"es"`, `"pt"`, or `"none"` to skip normalization. `ptts` itself has no default (the spoken forms of numbers, `@`, `+`, `=` differ per language); the module defaults to `"en"` because the default checkpoint is English-only, so the zero-config case speaks correctly. It does not pick the model: a language the checkpoint was not trained for only makes `ptts` emit a `UserWarning`, which the module lets through. Set it together with `model` when using a non-English checkpoint. |
| `model` | non-empty string | no | — (`ptts.DEFAULT_MODEL`, `hf://gradium/phonon-31m-en` at a pinned revision) | The checkpoint: `hf://owner/name` or `hf://owner/name@revision`, or a local model directory or `config.json`. A value not starting with `hf://` is a local path, resolved with `resolve_path` ([tts-module-interface.md](../tts-module-interface.md), "Path resolution"). |
| `voice` | non-empty string | no | — (the checkpoint's `default` voice) | A voice name — `default`, or a name from the Phonon voice catalog (`Juniper`, `Harper`, `Miles`, …), matched case- and accent-insensitively and downloaded on first use — or a local voice-embedding file ending in `.safetensors`, resolved with `resolve_path`. |
| `device` | non-empty string | no | `"auto"` | `"auto"` lets `ptts` pick the first entry of `ptts.available_devices()` (Metal on an Apple-silicon Mac, CPU elsewhere); any other value (`"cpu"`, `"metal"`, …) is passed through, and a device the installed build lacks is rejected at construction. |
| `quant` | non-empty string | no | — (the checkpoint's own weights) | Weight format, e.g. `"q8_0"`, `"q4k"` — one of `ptts.available_quants()`. Quantization is **CPU-only** in `ptts`: combined with a GPU device it is rejected at construction. |
| `temperature` | finite number ≥ 0 | no | — (`ptts` default 0.3) | Sampling temperature. |
| `cfg_coef` | number | no | — (checkpoint default) | Classifier-free-guidance coefficient (voice adherence). |

There is no API key (`api_key` / `api_key_env`): weights come from the Hugging Face Hub. The gated checkpoints (below) read the standard `HF_TOKEN` environment variable through the Hub client; the module adds nothing for it.

Fields left unset are **omitted** from the `ptts.TTS(...)` call, so the library's defaults apply and the module never hardcodes a copy of them.

### Checkpoints and licenses

| Checkpoint | Languages | License | Access |
|---|---|---|---|
| `gradium/phonon-31m-en` (default) | English | Apache-2.0 | public |
| `gradium/phonon-40m-{en,fr,de,es,pt}` | one each | CC BY-NC 4.0 (commercial use through Gradium) | gated, needs `HF_TOKEN` |
| `gradium/phonon-40m-multilingual` | en, fr, de, es, pt | CC BY-NC 4.0 | gated, needs `HF_TOKEN` |

Speaking a language other than English therefore needs a non-commercial checkpoint; the spec does not hide that behind a default. The catalog holds 102 voices across the five languages (37 English) as of `ptts` 0.4.2; the 31M checkpoint does not support voice cloning (`supports_voice_cloning` is false).

### Validation

Validated at construction, raising `ConfigError` before the model is loaded:

- `language` — if present, one of `en`, `fr`, `de`, `es`, `pt`, `none` (defaults to `en` when absent).
- `model`, `voice`, `device`, `quant` — if present, non-empty strings.
- `temperature` — if present, a finite number ≥ 0; `cfg_coef` — if present, a finite number. Booleans are rejected rather than treated as numbers, and so are `NaN` and the infinities (which JSON's parser accepts): they would reach the sampler.

`ptts` validates the rest against the actual build and checkpoint, and the module turns those rejections into `ConfigError` as well (see "Error handling"): an unavailable device, a GPU device with a quantization, an unknown voice name, a missing local file.

## Implementation notes

### Dependencies and lazy import

Requires the `phonon` extra: `pip install tts-engine[phonon]` / `uv sync --extra phonon`, declared as `phonon = ["ptts>=0.4.2,<0.5"]` and added to `all`. The upper bound is deliberate: `ptts` is pre-1.0 and has shipped eight releases since July 2026, and the module's calls are validated against 0.4.2 only. No torch, no `espeak-ng`, no system package.

**Platforms.** `ptts` 0.4.2 publishes `cp39-abi3` wheels for manylinux and musllinux x86_64/aarch64, macOS arm64 and x86_64, and Windows x86_64/arm64; the x86 builds require AVX2. So the extra installs on Intel macOS, unlike `pocket` ([project.md](../project.md), "Platforms").

`phonon/module.py` must not `import ptts` at file top (the registry imports every module class). The import happens inside `__init__`:

```python
try:
    import ptts
except ImportError as exc:
    raise ConfigError(
        "The 'phonon' module requires the phonon extra: pip install tts-engine[phonon]"
    ) from exc
```

### Construction (eager model load)

The engine reads `sample_rate` once at construction, so the model is loaded in `__init__`, after the lazy import and validation:

1. Build the keyword arguments from the fields that were set: `lang` always (the configured or default language; `None` for `"none"`); `model` (the `hf://` value as-is, or the `resolve_path` result as a string); `device` unless `"auto"`; `quant`, `temperature`, `cfg_coef` when set; `voice` when it is a **name**.
2. `self._tts = ptts.TTS(**kwargs)`. Weights download to the Hugging Face cache on first use (~160 MB for the default checkpoint); afterwards construction takes a fraction of a second. Passing a voice name here downloads that catalog voice now, so an unknown name fails at construction rather than on the first utterance.
3. A `.safetensors` voice is registered after loading: `self._tts.add_voice(name, path)`, where `name` is the file's stem.
4. Record `self._voice` (the name, the file's stem, or `None` for the default) and `self._sample_rate = int(self._tts.sample_rate)`.

`ptts.set_num_threads` is process-global and only takes effect before the first model load, so v1 does not expose it (see "Open questions").

### Sample rate

`sample_rate` returns `self._tts.sample_rate`: **24000 Hz** for every published checkpoint. No resampling.

### Streaming and format conversion

`self._tts.stream(text, voice=self._voice)` returns a `ptts.AudioStream`: a context manager and iterator of **1-D `numpy.float32`** chunks, produced by the runtime's own decoder threads. Measured on 0.4.2: fixed **1920-sample (80 ms)** chunks, peak amplitude well inside ±1, time to first chunk ~10 ms warm on an Apple M5 CPU, ~30× real time. Unread, the stream pauses once its bounded buffers fill; **leaving the `with` block (or `close()`) stops generation and joins the decoder threads** (~2 ms measured), after which `next` raises `StopIteration`.

The generate-and-feed loop for one text, shared by both input shapes:

```python
def _generate(self, text, callback, stop):
    try:
        audio = self._tts.stream(text, voice=self._voice)
    except Exception as exc:
        raise TTSError(f"phonon synthesis failed: {exc}") from exc
    with audio:  # exit stops the decoder threads, on every path
        while not stop.is_set():
            # Advance inside the provider try so runtime errors become
            # TTSError; feed outside it so a playback failure keeps its identity.
            try:
                chunk = next(audio)
            except StopIteration:
                break
            except Exception as exc:
                raise TTSError(f"phonon synthesis failed: {exc}") from exc
            callback(_to_int16_pcm(chunk))
```

`_to_int16_pcm` reshapes to 1-D, clips to `[-1.0, 1.0]`, scales by `32767`, casts to little-endian `int16` and returns `.tobytes()` — the same conversion as pocket, minus the torch-to-numpy step. No gain is applied: the model's level is what plays.

`synthesize_text` runs `_generate(text, callback, stop)` through `run_cancellable_worker`. The `ptts.TTS` object is built on the constructing thread and used from the worker thread; this was verified on 0.4.2 (the extension is not thread-bound and releases the GIL while decoding, so the engine's event loop keeps running).

### Text stream: sentence grouping

`ptts` has no incremental text API: `stream(text)` streams **audio** out but needs the whole text in (the Rust WebSocket server streams text, but it is a separate process, out of scope). `synthesize_text_stream` therefore follows pocket: one worker from `run_text_stream_worker`, iterating `segment_sentences(pieces)` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers") and running `_generate` once per sentence. Phonon's warm time to first chunk (~10 ms) makes the per-sentence call cost negligible, so sentence one plays while the stream is still delivering sentence two at effectively no latency penalty. `ptts` normalizes each sentence on its own; numbers and abbreviations inside a sentence are unaffected by the cut.

### Cancellation

Cooperative, as for every module: `stop` is polled between chunks, and the stream is closed (its `__exit__`) on every path through the shared `cleanup` helper, so the decoder threads are stopped before `_generate` returns or raises and a failing close never replaces the utterance's own error. No callback can occur after the coroutine exits.

### Error handling

At construction:

- A missing extra → `ConfigError` with the install hint (above).
- A field failing the module's own validation → `ConfigError`.
- `ptts` rejecting a value — `ValueError` (language), `NotImplementedError` (device not in this build, quantization on a GPU device), `LookupError` (unknown voice name), `FileNotFoundError` (local model or voice file) — → `ConfigError`, chained, naming the field where it can.
- Any other failure while loading (network or Hub errors, an inaccessible gated checkpoint, a corrupt file) → `TTSError(f"phonon model load failed: {exc}")`, chained — as pocket does.

At synthesis:

- Any exception from `stream()` or from advancing the `AudioStream` → `TTSError(f"phonon synthesis failed: {exc}")`, chained. This includes `ptts`'s `ValueError` for a text that is empty after its own normalization.
- Exceptions raised by `callback` propagate unchanged; the `with` block still closes the stream.
- Text-stream failures propagate unchanged through `run_text_stream_worker`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` drops the `ptts` runtime so its weights can be collected; the module is not usable afterwards.

## Module ID

Registered in `REGISTRY` as `"phonon"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)); class `PhononModule` in `modules/phonon/module.py`, re-exported from `modules/phonon/__init__.py`.

## Example config

`examples/config.phonon.json`:

```json
{
  "engine": {
    "module": {
      "type": "phonon",
      "voice": "Juniper"
    },
    "player": { "device": null }
  },
  "server": { "host": "127.0.0.1", "port": 8000 }
}
```

## Testing

- **Unit (`tests/modules/test_phonon.py`)** — `ptts` is **faked** (a stub module with a `TTS` class injected through `monkeypatch.setitem(sys.modules, "ptts", ...)`), so the fast tier never loads a model or touches the Hub. The fake `TTS` records its constructor kwargs and `add_voice` calls, and its `stream()` returns a fake `AudioStream` (context manager + iterator over given float32 arrays) that records `close`. Covers:
  - `ConfigError` with the install hint when `ptts` is unimportable;
  - validation: `language` unknown or not a string (absent defaults to `lang="en"`), empty `model`/`voice`/`device`/`quant`, negative, boolean or non-finite `temperature`, boolean or non-finite `cfg_coef`;
  - kwargs: only set fields are passed, `"auto"` omits `device`, `"none"` passes `lang=None`, an `hf://` model passes through, a relative local model resolves against `base_dir`;
  - a relative `.safetensors` voice resolves against `base_dir`, is registered with `add_voice(stem, path)`, and is not passed to the constructor; a voice name is;
  - library rejections (`ValueError`, `NotImplementedError`, `LookupError`, `FileNotFoundError` from the fake constructor) → `ConfigError`; another exception → `TTSError`;
  - `sample_rate` reflects the fake's rate;
  - `synthesize_text`: float chunks (including out-of-range values) become whole little-endian int16 samples, clipped; `stream` gets the text and the configured voice; the stream is closed afterwards;
  - an error while iterating → `TTSError` and the stream closed; a callback error propagates unchanged and the stream is closed, and survives a failing close;
  - cancellation while a chunk is pending closes the stream before `CancelledError` surfaces (event-driven, no sleeps);
  - the same for `synthesize_text_stream`, cancelled mid-sentence with the source still open: the stream is closed, no callback runs after `CancelledError` surfaces;
  - `synthesize_text_stream`: one `stream()` call per sentence, and audio reaches the callback before the source finishes (the shared test shape from the streaming-text-input plan).
- **Lazy imports (`tests/modules/test_lazy_imports.py`)** — `ptts` joins the poisoned libraries, `phonon` joins the expected registry keys, and constructing `{"type": "phonon"}` must raise the `tts-engine[phonon]` hint.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row, `pytest.param("phonon", {}, id="phonon")`, plus `_REQUIRED_IMPORT["phonon"] = "ptts"`, so the three conformance scenarios run against the real runtime and default checkpoint. No key is needed, so the row **runs in CI** (the extra comes with the `dev` group); its weights land in the Hugging Face cache the live job already caches. Robust properties only (PCM produced, drained once), never audio content.

## Validated against `ptts==0.4.2`

Confirmed on macOS arm64 (Apple M5 Pro), from the wheel's type stub and by running it:

- `ptts.TTS(model=None, device=None, quant=None, voice=None, temperature=0.3, seed=..., cfg_coef=None, eos_threshold=None, *, lang, rewrites=None, conditions=None, voice_catalog=None, config=None, revision=None)`; `lang` is keyword-only and required.
- `DEFAULT_MODEL = hf://gradium/phonon-31m-en@8c64b55…`, `DEFAULT_VOICE_CATALOG = hf://gradium/phonon-voices@da6c9ec…`. The checkpoint registers only `default`; catalog voices join `tts.voices` once used.
- `available_devices()` → `["metal", "cpu"]` on that wheel; `available_quants()` → `f32, q8_0, q8_1, q8k, q6k, q5_0, q5_1, q5k, q4_0, q4_1, q4k`. The default load is `f32`.
- `sample_rate == 24000`; `stream()` yields 1-D `float32` chunks of 1920 samples; `synth()` returns one 1-D `float32` array.
- Errors: unknown language → `ValueError`; `device="cuda"` → `NotImplementedError`; `quant` with `device="metal"` → `NotImplementedError`; unknown voice → `LookupError`; missing voice file → `FileNotFoundError`; unknown Hub repo → `OSError`; empty text → `ValueError`.
- `AudioStream.close()` returned in ~2 ms mid-generation; an exception raised inside `with tts.stream(...)` propagates unchanged.
- A `TTS` built on one thread streams correctly from another, and the GIL stays available to other threads while it decodes.

Not verified on Linux or Windows: the device list of those wheels (assumed `["cpu"]`) and the timings.

## Open questions

Deferrals only (design is settled):

1. **Thread count.** `ptts.set_num_threads(n)` sizes a process-global pool once, before the first model load; a per-module field cannot honor it reliably when several engines share a process. Add a field later if wanted, documented as process-global.
2. **More `ptts` knobs.** `seed`, `eos_threshold`, `rewrites`, `conditions` and a custom `voice_catalog` are omitted from v1; each is a plain constructor keyword, so adding one is a config field plus a validation rule.
3. **Voice cloning.** `clone_voice(name, pcm)` exists but the default checkpoint cannot clone; revisit if a public cloning checkpoint ships.
4. **Text-in streaming.** If `ptts` gains an incremental text API in Python (the Rust server already streams text), `synthesize_text_stream` can switch from sentence grouping to a native stream.
