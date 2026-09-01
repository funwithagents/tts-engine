---
code:
  - src/tts_engine/modules/base.py
  - src/tts_engine/modules/__init__.py
tests:
  - tests/modules/test_tts_module_interface.py
---

# TTS Module Interface

**Status:** Implemented

## Purpose

The `TTSModule` ABC defines the contract every TTS backend must implement. It decouples the engine and server from any specific provider.

## Dataclasses

### `TTSOptions`

Carries per-call synthesis parameters. Currently empty — all synthesis options (voice, model, etc.) come from the module's config. Reserved for future per-call overrides (e.g. speed, language).

```python
@dataclass
class TTSOptions:
    pass
```

Module-specific configuration is not modeled as a dataclass: each module's constructor takes the raw module config `dict` (the `engine.module` block, see [configuration.md](configuration.md)) and validates it directly (see the ABC below).

## `TTSModule` ABC

```python
from abc import ABC, abstractmethod
from collections.abc import Callable


class TTSModule(ABC):
    def __init__(self, config: dict) -> None:
        """Construct from the module config block (the `engine.module` dict,
        including `type`). Subclasses validate and extract the fields they
        need, raising `ConfigError` for missing/invalid ones."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """The sample rate (Hz) of the PCM the module feeds to `callback`.

        Declared by the module and fixed for its lifetime — the engine reads it
        once and opens the `AudioPlayer` output stream at this rate (see the
        audio format contract below). A module must resample internally if its
        backend's native rate differs from the value returned here."""

    @abstractmethod
    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize `text` and call `callback` with each PCM chunk as it arrives.

        - `text`: the string to synthesize. Must not be empty.
        - `options`: per-call overrides; currently empty and reserved for future use.
        - `callback`: called sequentially with each raw audio chunk. Calls may run
          off the event-loop thread but must never overlap.

        Raises `TTSError` on synthesis failure.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """
```

## Module registry

`modules/__init__.py` maintains a `REGISTRY` dict mapping type strings to module classes:

```python
REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,
}
```

`load_module(tts_config: dict) -> TTSModule` reads `tts_config["type"]`, looks it up in `REGISTRY`, and constructs the module with the complete config dictionary, including `type`. Raises `ConfigError` for unknown types.

## Audio format contract

All modules **must** feed `callback` raw PCM in this format:

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit PCM (little-endian) |
| Channels | 1 (mono) |
| Sample rate | Declared by the module via the `sample_rate` property (Hz) |

Encoding and channel count are fixed. **Sample rate is module-declared**: each module returns its rate from `sample_rate`, and the engine opens the `AudioPlayer` output stream at that rate (see [audio-player.md](audio-player.md) and [architecture.md](architecture.md)). This lets API providers that stream at 44100 Hz (ElevenLabs) and local models with a native rate of, say, 24000 Hz coexist without a project-wide resample. A module whose backend emits a different rate than it declares must resample internally before the callback.

Modules must not emit MP3 or other encoded formats without an explicit decoding step, and must not emit float samples — convert to signed 16-bit before the callback.

## Local-model modules

The API-backed pattern (ElevenLabs) is one shape; a second shape is a **local-model module** that runs inference in-process (e.g. Kokoro, ChatTTS, Piper). The [`huggingface/speech-to-speech`](https://github.com/huggingface/speech-to-speech/tree/main/src/speech_to_speech/TTS) TTS handlers are a useful catalogue of which engines work well and how to invoke them, but they are **not** reusable as-is: each extends that project's `BaseHandler[TTSIn, TTSOut]` pipeline (a `setup()`/queue/`CancelScope` framework), yields `numpy` arrays at 16 kHz, and pulls in the whole app package. A local-model module here wraps the **underlying model library directly** (e.g. the `kokoro` package), not the handler, and honors this interface:

- **Config-driven construction.** `__init__` validates the `engine.module` block and loads the model (or defers the load to first use). Model download / device selection are config fields, mirroring `api_key_env`/`voice_id` on ElevenLabs.
- **`sample_rate` reports the module's native rate** (commonly 24000 for these models), so no resampling is needed unless the backend itself varies.
- **Inference runs off the event loop.** Local inference is blocking and CPU/GPU-bound; run it inside `asyncio.to_thread` (as ElevenLabs does for its blocking SDK iterator), driving `callback` from that single worker thread so calls never overlap.
- **Float → int16 conversion** happens in the module before the callback (clip to ±1.0, scale to the int16 range), since these libraries yield float waveforms.
- **Cancellation and errors** follow the same rules as any module: stop calling `callback` before returning/raising, and wrap backend/inference failures in `TTSError`.

Concrete config fields and dependencies for a specific local-model module are specced alongside its code when it lands, following this contract. Because these backends pull in heavy libraries (`torch` etc.), each ships behind its own packaging **extra** and imports that library lazily — see [project.md](project.md), "Dependency strategy for TTS backends".

## Error handling

- Modules raise `TTSError` (defined in `modules/base.py`) for provider and decoding failures (API errors, network errors, invalid responses).
- `ConfigError` is raised in the constructor for invalid or missing config fields.
- Callback failures belong to the downstream consumer and must propagate unchanged rather than being mislabeled as provider failures.
- Modules must not swallow exceptions silently.
