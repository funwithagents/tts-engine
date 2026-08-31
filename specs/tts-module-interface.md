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
        - `options`: per-call overrides (voice, etc.). Module uses config defaults for None fields.
        - `callback`: called once per audio chunk with raw bytes. May be called from any thread.

        Raises `TTSError` on synthesis failure.
        Returns only after all chunks have been passed to `callback`.
        """
```

## Module registry

`modules/__init__.py` maintains a `REGISTRY` dict mapping type strings to module classes:

```python
REGISTRY: dict[str, type[TTSModule]] = {
    "elevenlabs": ElevenLabsModule,
}
```

`load_module(tts_config: dict) -> TTSModule` reads `tts_config["type"]`, looks it up in `REGISTRY`, and constructs the module with the remaining config fields. Raises `ConfigError` for unknown types.

## Audio format contract

All modules **must** produce audio in the following format unless the `AudioPlayer` spec is updated:

| Property | Value |
|----------|-------|
| Encoding | Signed 16-bit PCM (little-endian) |
| Sample rate | 44100 Hz |
| Channels | 1 (mono) |

The AudioPlayer is configured to match this format. Modules must not emit MP3 or other encoded formats without an explicit decoding step.

## Error handling

- Modules raise `TTSError` (defined in `modules/base.py`) for synthesis failures (API errors, network errors, invalid responses).
- `ConfigError` is raised in the constructor for invalid or missing config fields.
- Modules must not swallow exceptions silently.
