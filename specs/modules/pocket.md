---
code:
  - src/tts_engine/modules/pocket/__init__.py
  - src/tts_engine/modules/pocket/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_pocket.py
  - tests-e2e/test_modules.py
---

# Pocket TTS Module

**Status:** Implemented

## Overview

`pocket` implements `TTSModule` using [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) — a family of small (2M to ~100M-parameter) **local** TTS models that run inference in-process on CPU or GPU. It is the reference **local-model** backend, following the pattern in [tts-module-interface.md](../tts-module-interface.md) ("Local-model modules"): the model library is wrapped directly (not the `huggingface/speech-to-speech` handler), inference runs off the event loop, and float waveforms are converted to signed-16-bit PCM before the callback.

Unlike ElevenLabs it needs **no API key** and makes **no network call at synthesis time** — but it pulls in `torch` and downloads model weights, so it ships behind a packaging **extra** and imports its library lazily (see "Dependencies" below and [project.md](../project.md), "Dependency strategy for TTS backends").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "pocket"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `voice` | non-empty string | no | — (the library's default voice for the model: `alba` for English, `estelle` French, `juergen` German, `giovanni` Italian, `rafael` Portuguese, `lola` Spanish, `daan` Dutch) | A pocket-tts voice: a preset name (`alba`, `marius`, `estelle`, …), a local audio file (`.wav`, `.mp3`, `.flac`) to clone, a local `.safetensors` voice state, or an `hf://`/`https://` URL to either. Resolved once at construction via `get_state_for_audio_prompt`. A value ending in one of those extensions that is not a URL is a file path, resolved with `resolve_path(config, voice)` ([tts-module-interface.md](../tts-module-interface.md), "Path resolution"), so a relative path is relative to the config file's directory (`base_dir`). Cloning from audio needs the gated weights (see "Voice cloning"). |
| `language` | non-empty string | no | — (library default `english`, currently the `english_2026-10` model) | The model, passed as `TTSModel.load_model(language=...)`: a language (`english`, `french`, `german`, `italian`, `portuguese`, `spanish`, `dutch`), a 24-layer variant (`<language>_24l`), a dated English model (`english_2026-10`, `english_2026-09`, …), or a tiny English model (`english_18m_2026-10`, `english_7m_2026-10`, `english_2m_2026-10`, and their `_refl_` siblings). The library validates the name; the module passes it through so new upstream models need no code change. Omitted from the call when unset. Mutually exclusive with `config`. |
| `config` | non-empty string | no | — | A custom model config YAML (community or fine-tuned weights): an `hf://owner/repo/file.yaml[@revision]` or `https://` URL, kept as-is, or a local path resolved with `resolve_path`. Passed as `load_model(config=...)`. Mutually exclusive with `language`. Preset voices do not work with custom weights: use an audio-file voice (the library's own default for a custom config). |
| `device` | string | no | `"auto"` | Compute device: `"auto"`, `"cpu"`, `"cuda"`, or `"mps"`. `"auto"` picks `cuda` when available, else `cpu` — and always `cpu` when `quantize` is set. `mps` is **not** auto-selected — it must be requested explicitly and is experimental (see the note below). |
| `quantize` | boolean | no | `false` | Dynamic int8 quantization of the transformer (`load_model(quantize=True)`): ~half the runtime memory, faster on x86 CPUs, speech quality unchanged per upstream. **CPU only**: combined with an explicit `cuda` or `mps` device it is rejected at construction. Uses `torchao` when installed, else torch's built-in `torch.ao` path; the extra does not pull in `torchao`. |
| `temperature` | number ≥ 0 | no | — (the model config's own value, 0.3 for the released models) | Sampling temperature, passed as `load_model(temp=...)`. |
| `max_tokens` | integer > 0 | no | — (library default, 50) | Per-*text-chunk* token cap, passed to `generate_audio_stream(..., max_tokens=...)`. The model splits text into chunks internally and this bounds tokens per chunk (too low a value makes it skip words); the library default (50) is its tuned value, so this is omitted from the call when unset — set it higher only for unusually long unbroken chunks. |

There is **no** `api_key`/`api_key_env`: weights come from the Hugging Face Hub, and the gated ones read the standard `HF_TOKEN` (or a local `hf auth login`) through the Hub client; the module adds nothing for it.

Fields left unset are **omitted** from the library calls, so the library's defaults apply and the module never hardcodes a copy of them. The default voice is the one exception the module has to resolve itself, because `get_state_for_audio_prompt` has no default: it asks the library (`pocket_tts.default_parameters.get_default_voice_for_language(language, config)`) rather than keeping its own table.

### Voice cloning

The released weights come in two variants: with voice cloning (gated on Hugging Face: accept the terms at [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts), then provide `HF_TOKEN` or log in) and without it (public). The library loads the cloning variant when it can and falls back silently otherwise. Preset voices are precomputed states and work with either; an audio-file or audio-URL voice needs the cloning variant, and without it `get_state_for_audio_prompt` raises `ValueError`, which the module reports as a `ConfigError` (see "Error handling"). A `.safetensors` voice state works with either.

### Validation

Validated at construction, raising `ConfigError` before the model is loaded:

- `voice` — if present, a non-empty string. A local file path (ends with `.wav`, `.mp3`, `.flac` or `.safetensors`, no `://`) is passed to the model as the absolute path `resolve_path` returns; presets and URLs are passed through untouched.
- `language`, `config` — if present, non-empty strings, and not both.
- `device` — if present, one of `"auto"`, `"cpu"`, `"cuda"`, `"mps"`.
- `quantize` — if present, a boolean; `true` with `device` `"cuda"` or `"mps"` is rejected.
- `temperature` — if present, a number ≥ 0; `max_tokens` — if present, an integer `> 0`. Booleans are rejected rather than treated as numbers.

The library validates the rest against its own catalogs, and the module turns those rejections into `ConfigError` too (see "Error handling"): an unknown `language`, a `config` that is not a YAML file, a missing local file, a preset voice with custom weights, an audio voice without the cloning weights.

## Implementation notes

### Dependencies and lazy import

Requires the `pocket` extra: `pip install tts-engine[pocket]` / `uv sync --extra pocket`, declared in `[project.optional-dependencies]` as `pocket = ["pocket-tts>=4.0.1,<5"]`. The floor is the first release with the `stop` event, the multi-language and tiny models and working quantization on the torch the project uses; the ceiling is because the module's calls are validated against 4.0.1 only, and the major bumps of this package have changed its defaults (4.0 swapped the default English model, so output drifts from 3.x). `pocket-tts` pulls in `torch>=2.5` (hundreds of MB to ~2 GB) plus a model download — far heavier than the framework, which is exactly why it is optional rather than a base dependency.

**Not on Intel macOS.** PyTorch publishes no x86_64 macOS wheel after torch 2.2, far below the version `pocket-tts` needs, so the `pocket` extra does not install on an Intel Mac — the resolver fails at install time, before the lazy-import hint could apply. Every other platform the project supports has a torch wheel: macOS arm64, Windows x86_64, and Linux x86_64 and arm64 (from PyTorch's CPU index in this repo's own environment — see [project.md](../project.md), "Platforms").

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `pocket/module.py` must **not** `import torch`/`import pocket_tts` at file top — that would make `import tts_engine` and `load_module` require the extra. Instead the import happens inside `__init__`, turning a missing extra into an actionable `ConfigError`:

```python
try:
    import torch
    from pocket_tts import TTSModel
    from pocket_tts.default_parameters import get_default_voice_for_language
except ImportError as exc:
    raise ConfigError(
        "The 'pocket' module requires the pocket extra: pip install tts-engine[pocket]"
    ) from exc
```

### Construction (eager model load)

The engine reads `sample_rate` **once at construction** to open the `AudioPlayer` output stream, so the model must be loaded by then. `__init__` therefore, after config validation and the lazy import:

1. Resolves `device`: `"auto"` → `"cpu"` if `quantize`, else `"cuda"` if `torch.cuda.is_available()`, else `"cpu"`; an explicit value is used as-is.
2. Loads the model — `TTSModel.load_model(**kwargs)` with only the set fields among `language`, `config`, `temp`, `quantize` — and moves it to the device (`model.to(device)`; `TTSModel` is an `nn.Module`).
3. Resolves the voice once: `voice_state = model.get_state_for_audio_prompt(voice)`, with `voice` defaulting to `get_default_voice_for_language(language, config)`.
4. Records `model.sample_rate` for the `sample_rate` property.

One `log.info` after the load names the model (`language` or `config`, else the default), the device and whether quantization is on; nothing is logged per chunk.

Model download/load is blocking and takes a few seconds (weights are fetched to the Hugging Face cache on first run, then ~2–5 s warm, the tiny models faster); this is startup cost paid once, mirroring how ElevenLabs builds its client in `__init__`.

### Device note (mps is experimental)

`"auto"` deliberately does **not** select Apple's `mps` backend: on `pocket-tts` 4.0.1 with `torch` 2.14 it still aborts the process mid-generation with a Metal command-encoder assertion (`A command encoder is already encoding to this command buffer`), so auto-selecting it would break synthesis on Apple Silicon — the common dev machine. CPU there is fine (several times real-time on an M-series). `mps` remains reachable by setting `device: "mps"` explicitly for anyone wanting to test it, but it is unsupported until the upstream issue is resolved; `cuda` and `cpu` are the supported paths.

### Sample rate

`sample_rate` returns the model's **native** rate (`model.sample_rate`, 24000 Hz for every released model) — declared, fixed for the module's lifetime. **No resampling**: per the audio-format contract ([tts-module-interface.md](../tts-module-interface.md)), the engine opens the player at this rate, so the module feeds the model's native PCM straight through. (This is the deliberate difference from the `huggingface/speech-to-speech` handler, which resamples to 16 kHz for its own pipeline.)

### Streaming and format conversion

`generate_audio_stream(voice_state, text, max_tokens=..., stop=...)` returns a synchronous generator of 1-D `torch.float32` tensors — mono, native rate, values in ~`[-1, 1]`. Chunk sizes vary with the model (1920 samples, 80 ms, for the 100M models; several frames at once for the tiny ones), and the first chunk of each internal text chunk is faded in over 5 ms by the library; the module relies on neither. Like the ElevenLabs module, the whole generate-and-feed loop runs inside the cancellable worker so blocking inference never stalls the event loop, driving `callback` from that single worker thread (calls never overlap).

The worker's `stop` event ([tts-module-interface.md](../tts-module-interface.md), "Cancellation") is passed **straight into** `generate_audio_stream`: once set, the library stops its generation thread before the next frame and ends the iterator, so a cancellation stops inference mid-sentence as promptly as between sentences, instead of leaving the generation thread running to the sentence's end. The library's decoder may still hand back the few frames it had already queued; the loop checks `stop` before each `next` too, so none of them reaches the callback.

```python
def _generate(self, text, callback, stop):
    kwargs = {"stop": stop}
    if self._max_tokens is not None:
        # Only when configured, else the library's own default (50) applies.
        kwargs["max_tokens"] = self._max_tokens
    stream_iter = self._model.generate_audio_stream(self._voice_state, text, **kwargs)
    while not stop.is_set():
        # Advance the generator inside the provider try so inference
        # errors become TTSError; feed the callback outside it so a
        # downstream playback failure keeps its own identity.
        try:
            chunk = next(stream_iter)
        except StopIteration:
            break
        except Exception as exc:
            raise TTSError(f"pocket-tts synthesis failed: {exc}") from exc
        callback(_to_int16_pcm(chunk))  # float tensor -> signed-16 LE bytes
```

`synthesize_text` runs `_generate` once in `run_cancellable_worker`.

### Text stream: sentence grouping

pocket-tts has no incremental text API: the model conditions on its whole prompt before emitting a frame, and `generate_audio_stream` itself splits a long text into sentences (`split_into_best_sentences`) and generates each one from a fresh copy of the voice state. `synthesize_text_stream` therefore does the same split **as the text arrives**: one worker from `run_text_stream_worker`, iterating `segment_sentences(pieces)` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers") and running `_generate` once per sentence, inside that single worker. Audio for sentence one plays while the stream is still delivering sentence two; the result is equivalent to what the library does internally for a complete text, only earlier. A sentence longer than `max_tokens` is still split further by the library, as for `synthesize_text`. Error and cancellation rules are those of `synthesize_text`, plus: a text-stream failure aborts via the bridge and propagates unchanged.

**Float → int16** happens in the module before the callback: move to CPU/`numpy`, clip to `[-1.0, 1.0]`, scale by `32767`, cast to little-endian `int16`, and emit `.tobytes()`. This satisfies the "signed 16-bit PCM (little-endian), mono" half of the audio-format contract.

### Error handling

- A missing extra surfaces as `ConfigError` at construction (see above), not `TTSError`.
- At load, the library rejecting a value — `ValueError` (a config that is not YAML, a preset voice with custom weights, an audio voice without the cloning weights) or `FileNotFoundError` (an unknown `language`, whose config file the library does not ship, or a missing local config or voice file) — becomes `ConfigError(f"Pocket module: {exc}")`, chained. Any other load exception (a Hub download failure, a corrupt checkpoint) becomes `TTSError(f"pocket-tts model load failed: {exc}")`. The `ValueError` branch comes first; the module's own validation runs outside that `try`, since `ConfigError` is itself a `ValueError`.
- Any exception from inference is caught and re-raised as `TTSError(f"pocket-tts synthesis failed: {exc}")`, chained via `raise ... from exc` — same discipline as ElevenLabs.
- Exceptions raised by `callback` are downstream playback failures: they propagate unchanged and must not be relabeled as synthesis failures — `callback` is invoked outside the `try` that wraps the generator's `next(...)`, so its exceptions escape the provider boundary (mirroring the ElevenLabs note in [elevenlabs-module.md](elevenlabs.md)).

## Module ID

Registered in `REGISTRY` as `"pocket"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_pocket.py`)** — the `pocket_tts` library is **faked** (a stub `TTSModel` and `default_parameters` injected via `sys.modules`/`monkeypatch`), never importing `torch`, so the fast tier stays runnable without the `pocket` extra and never waits on a model load — even though `uv sync --dev` now installs the extra (see [project.md](../project.md), "Key dependencies"). Covers: `ConfigError` on a missing extra (import failure) and on invalid `device`/`max_tokens`/`voice`/`quantize`/`temperature`, `language` with `config`, and `quantize` with a GPU device; `device` auto-detection, and `auto` resolving to `cpu` under `quantize`; only set fields forwarded to `load_model` (`temperature` as `temp`); the default voice asked of the library for the configured language/config; local voice files and a local `config` resolved against `base_dir`, URLs untouched; a library `ValueError` at load becoming `ConfigError` and another exception `TTSError`; `sample_rate` reflecting the (faked) model's rate; `synthesize_text` converting fake float chunks to whole-int16 PCM, passing the worker's `stop` event to `generate_audio_stream`, and wrapping backend errors in `TTSError`; and `synthesize_text_stream` calling the fake's `generate_audio_stream` once per sentence (texts recorded by the fake), with the first sentence's audio reaching the callback before the source has yielded the second (the source waits on an event the callback sets); cancellation mid-sentence, with the source still open, stops callbacks before `synthesize_text_stream()` raises and sets the event the fake received. Follows the ElevenLabs unit-test shape (mock the library, assert observable behavior).
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`pocket`, no `api_key_env`) plus `_REQUIRED_IMPORT["pocket"] = "pocket_tts"` in `support.py`, so the per-module scenarios run against the real model and **skip cleanly** when the extra isn't installed (see [testing.md](../testing.md), "Live tier"). Asserts robust properties only (PCM produced, drained once, playback completes, cancellation stops audio), never audio content.

## Validated against `pocket-tts==4.0.1`

The API was confirmed against the pinned package (`torch==2.14.0`, Apple M-series CPU):

- `TTSModel.load_model(language=None, config=None, temp=None, sampler_decode_steps=1, noise_clamp=None, eos_threshold=None, quantize=False, checkpoint=None, ...)` → `Self`; `language` and `config` together raise `ValueError`; `TTSModel` is an `nn.Module` (`.to(device)`, `.device`, `.sample_rate` all present).
- `model.sample_rate == 24000` for the default, `french`, `english_2m_2026-10` and quantized models.
- Language configs shipped: `english` (= `english_2026-10`), `english_2026-01/-04/-09/-09_2/-10`, `english_2026-04_24l`, `english_2026-09_24l`, `english_drifting_26-09`, `english_{18m,7m,2m}[_refl]_2026-10`, and `french`, `german`, `italian`, `portuguese`, `spanish`, `dutch`, each with a `_24l` variant.
- `get_state_for_audio_prompt(audio_conditioning: Path | str | torch.Tensor, truncate=False)` accepts a **predefined voice name** (27 presets, including `estelle` and `daan`), a local/remote audio file, an `hf://`/https URL, or a `.safetensors` state; audio prompts raise `ValueError` without the gated cloning weights (`model.has_voice_cloning`).
- `pocket_tts.default_parameters.get_default_voice_for_language(language, config=None, checkpoint=None)` returns the preset for a language (`alba` as fallback) and the `alba` audio URL for custom weights.
- `generate_audio_stream(state, text, max_tokens=50, frames_after_eos=None, copy_state=True, stop=None) -> Iterator[torch.Tensor]`, yielding 1-D `float32` mono chunks at 24 kHz (1920 samples for the 100M models, 5760–7680 for `english_2m`). Setting `stop` after the fourth chunk ended the iterator after one or two more (already decoded) chunks.
- `quantize=True` loads and generates on CPU through the `torch.ao` fallback (no `torchao` installed), with a deprecation warning from torch.
- An unknown `language` raises `FileNotFoundError` (`Config file not found`).
- `mps` still aborts with the Metal command-encoder assertion.

## Open questions

Deferrals only (design is settled):

1. **`mps` support.** Blocked upstream (Metal command-encoder assertion mid-generation, still on 4.0.1 / torch 2.14). Re-enable `mps` in `"auto"` once a `pocket-tts`/`torch` release fixes it; until then it's explicit-only and experimental.
2. **Generation knobs.** `eos_threshold`, `noise_clamp`, `sampler_decode_steps`, `frames_after_eos` and the training `checkpoint` stay out until someone needs them; each would be one more omitted-when-unset argument.
3. **`torchao` for quantization.** The extra does not depend on `pocket-tts[quantize]`; the built-in `torch.ao` path works but is deprecated in torch. Revisit if torch drops it.
