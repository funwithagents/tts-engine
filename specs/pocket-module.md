---
code:
  - src/tts_engine/modules/pocket.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_pocket.py
  - tests-e2e/test_modules.py
---

# Pocket TTS Module

**Status:** Implemented

## Overview

`pocket` implements `TTSModule` using [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) — a small (~100M-parameter) **local** TTS model that runs inference in-process on CPU or GPU. It is the reference **local-model** backend, following the pattern in [tts-module-interface.md](tts-module-interface.md) ("Local-model modules"): the model library is wrapped directly (not the `huggingface/speech-to-speech` handler), inference runs off the event loop, and float waveforms are converted to signed-16-bit PCM before the callback.

Unlike ElevenLabs it needs **no API key** and makes **no network call at synthesis time** — but it pulls in `torch` and downloads model weights, so it ships behind a packaging **extra** and imports its library lazily (see "Dependencies" below and [project.md](project.md), "Dependency strategy for TTS backends").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "pocket"` (see [configuration.md](configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `voice` | non-empty string | no | `"alba"` | A pocket-tts voice: a preset name (`alba`, `giovanni`, `lola`, …), a local `.wav` path, or a Hugging Face URL. Resolved once at construction via `get_state_for_audio_prompt`. |
| `language` | non-empty string | no | — (model default `english`) | Language passed to `TTSModel.load_model(language=...)`: `english`, `german`, `italian`, `portuguese`, `spanish`, or `french_24l` (plain `french` raises — a 24-layer model is required). Omitted from the call when unset. |
| `device` | string | no | `"auto"` | Compute device: `"auto"`, `"cpu"`, `"cuda"`, or `"mps"`. `"auto"` picks `cuda` when available, else `cpu`. `mps` is **not** auto-selected — it must be requested explicitly and is experimental (see the note below). |
| `max_tokens` | integer > 0 | no | — (library default, 50) | Per-*text-chunk* token cap, passed to `generate_audio_stream(..., max_tokens=...)`. The model splits text into chunks internally and this bounds tokens per chunk (too low a value makes it skip words); the library default (50) is its tuned value, so this is omitted from the call when unset — set it higher only for unusually long unbroken chunks. |

There is **no** `api_key`/`api_key_env` (no key), and **no** quantization field in v1 (the `quantize`/`torchao` path is deferred — see "Open questions").

### Validation

Validated at construction, raising `ConfigError` before the model is loaded:

- `voice` — if present, a non-empty string (defaults to `"alba"` when absent).
- `language` — if present, a non-empty string.
- `device` — if present, one of `"auto"`, `"cpu"`, `"cuda"`, `"mps"`.
- `max_tokens` — if present, an integer `> 0`; booleans are rejected rather than treated as integers.

## Implementation notes

### Dependencies and lazy import

Requires the `pocket` extra: `pip install tts-engine[pocket]` / `uv sync --extra pocket`, declared in `[project.optional-dependencies]` as `pocket = ["pocket-tts>=<pinned>"]`. `pocket-tts` pulls in `torch>=2.5` (hundreds of MB to ~2 GB) plus a model download (~100M params) — far heavier than the framework, which is exactly why it is optional rather than a base dependency.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `pocket.py` must **not** `import torch`/`import pocket_tts` at file top — that would make `import tts_engine` and `load_module` require the extra. Instead the import happens inside `__init__`, turning a missing extra into an actionable `ConfigError`:

```python
try:
    from pocket_tts import TTSModel
except ImportError as exc:
    raise ConfigError(
        "The 'pocket' module requires the pocket extra: pip install tts-engine[pocket]"
    ) from exc
```

### Construction (eager model load)

The engine reads `sample_rate` **once at construction** to open the `AudioPlayer` output stream, so the model must be loaded by then. `__init__` therefore, after the lazy import and config validation:

1. Resolves `device`: `"auto"` → `"cuda"` if `torch.cuda.is_available()` else `"cpu"`; an explicit value is used as-is.
2. Loads the model — `TTSModel.load_model(language=language)` (language omitted when unset) — and moves it to the device (`model.to(device)`; `TTSModel` is an `nn.Module`).
3. Resolves the voice once: `voice_state = model.get_state_for_audio_prompt(voice)`.
4. Records `model.sample_rate` for the `sample_rate` property.

Model download/load is blocking and takes ~10 s on first run (weights are fetched to the Hugging Face cache, then ~seconds warm); this is startup cost paid once, mirroring how ElevenLabs builds its client in `__init__`.

### Device note (mps is experimental)

`"auto"` deliberately does **not** select Apple's `mps` backend: on the pinned `pocket-tts`/`torch` it aborts mid-generation with a Metal command-encoder assertion, so auto-selecting it would break synthesis on Apple Silicon — the common dev machine. CPU there is fine (~10× real-time on an M-series). `mps` remains reachable by setting `device: "mps"` explicitly for anyone wanting to test it, but it is unsupported until the upstream issue is resolved; `cuda` and `cpu` are the supported paths.

### Sample rate

`sample_rate` returns the model's **native** rate (`model.sample_rate`, typically 24000 Hz) — declared, fixed for the module's lifetime. **No resampling**: per the audio-format contract ([tts-module-interface.md](tts-module-interface.md)), the engine opens the player at this rate, so the module feeds the model's native PCM straight through. (This is the deliberate difference from the `huggingface/speech-to-speech` handler, which resamples to 16 kHz for its own pipeline.)

### Streaming and format conversion

`generate_audio_stream(voice_state, text, max_tokens=...)` returns a synchronous generator of 1-D `torch.float32` tensors — mono, native rate, ~1920 samples (80 ms) per chunk, values in ~`[-1, 1]`. Like the ElevenLabs module, the whole generate-and-feed loop runs inside `asyncio.to_thread` so blocking inference never stalls the event loop, driving `callback` from that single worker thread (calls never overlap):

```python
async def stream(self, text, options, callback):
    def _blocking_stream():
        try:
            for chunk in self._model.generate_audio_stream(
                self._voice_state, text, max_tokens=self._max_tokens
            ):
                pcm = _to_int16_pcm(chunk)  # float tensor -> signed-16 LE bytes
                callback(pcm)
        except Exception as exc:
            raise TTSError(f"pocket-tts synthesis failed: {exc}") from exc

    await asyncio.to_thread(_blocking_stream)
```

**Float → int16** happens in the module before the callback: move to CPU/`numpy`, clip to `[-1.0, 1.0]`, scale by `32767`, cast to little-endian `int16`, and emit `.tobytes()`. This satisfies the "signed 16-bit PCM (little-endian), mono" half of the audio-format contract.

### Error handling

- Any exception from the model/inference is caught and re-raised as `TTSError(f"pocket-tts synthesis failed: {exc}")`, chained via `raise ... from exc` — same discipline as ElevenLabs.
- A missing extra surfaces as `ConfigError` at construction (see above), not `TTSError`.
- Exceptions raised by `callback` are downstream playback failures: they propagate unchanged and must not be relabeled as synthesis failures — `callback` is invoked outside the `try` that wraps the generator's `next(...)`, so its exceptions escape the provider boundary (mirroring the ElevenLabs note in [elevenlabs-module.md](elevenlabs-module.md)).
- The generate-and-feed loop runs inside a single `asyncio.to_thread` worker, the same threading model as the ElevenLabs module.

## Module ID

Registered in `REGISTRY` as `"pocket"` ([modules/__init__.py](../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_pocket.py`)** — the fast tier must stay installable without the `pocket` extra, so the `pocket_tts` library is **faked** (a stub `TTSModel` injected via `sys.modules`/`monkeypatch`), never importing `torch`. Covers: `ConfigError` on a missing extra (import failure) and on invalid `device`/`max_tokens`/`voice`; `device` auto-detection; `sample_rate` reflecting the (faked) model's rate; and `stream` converting fake float chunks to whole-int16 PCM and wrapping backend errors in `TTSError`. Follows the ElevenLabs unit-test shape (mock the library, assert observable behavior).
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`pocket`, no `api_key_env`) plus `_REQUIRED_IMPORT["pocket"] = "pocket_tts"` in `support.py`, so both per-module scenarios (`test_module_say_produces_pcm`, `test_module_say_completes`) run against the real model and **skip cleanly** when the extra isn't installed (see [testing.md](testing.md), "Live tier"). Asserts robust properties only (PCM produced, drained once, playback completes), never audio content.

## Validated against `pocket-tts==3.1.0`

The API was confirmed against the pinned package (`torch==2.14.0`):

- `TTSModel.load_model(language=None, quantize=False, checkpoint=None, ...)` → `Self`; `TTSModel` is an `nn.Module` (`.to(device)`, `.device`, `.sample_rate` all present).
- `model.sample_rate == 24000`.
- `get_state_for_audio_prompt(audio_conditioning: Path | str | torch.Tensor)` accepts a **predefined voice name** (26 presets: `alba`, `giovanni`, `lola`, `juergen`, `marius`, …), a local/remote `.wav`, an `hf://`/https URL, or a `.safetensors` state.
- `generate_audio_stream(state, text, max_tokens=50, ...) -> Iterator[torch.Tensor]`, yielding 1-D `float32` mono chunks (~1920 samples each) at 24 kHz; generation stops at natural EOS (~10× real-time on M-series CPU).

## Open questions

Deferrals only (design is settled):

1. **Quantization.** `load_model(quantize=True)` (and `pocket_tts.quantization`) offer int8 for CPU speed/size; intentionally out of v1. Add a `quantize` config flag later if wanted — it's a `load_model` argument, so likely no extra dependency, to confirm then.
2. **`mps` support.** Blocked upstream (Metal command-encoder assertion mid-generation on the pinned versions). Re-enable `mps` in `"auto"` once a `pocket-tts`/`torch` release fixes it; until then it's explicit-only and experimental.
