"""Pocket TTS local-model module.

Wraps kyutai-labs/pocket-tts directly (not the huggingface/speech-to-speech
handler): a ~100M-parameter model that runs in-process. `torch` and `pocket_tts`
are imported lazily inside `__init__` so `import tts_engine` and `load_module`
never require the `pocket` extra — a missing extra becomes a clear `ConfigError`.
"""

import threading
from collections.abc import Callable

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
)

_VALID_DEVICES = {"auto", "cpu", "cuda", "mps"}


def _to_int16_pcm(chunk) -> bytes:
    """Convert one 1-D float32 torch tensor (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = chunk.detach().to("cpu").numpy().reshape(-1).astype(np.float32)
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


class PocketModule(TTSModule):
    def __init__(self, config: dict) -> None:
        voice = config.get("voice", "alba")
        if not isinstance(voice, str) or not voice:
            raise ConfigError("Pocket module 'voice' must be a non-empty string")

        language = config.get("language")
        if language is not None and (not isinstance(language, str) or not language):
            raise ConfigError("Pocket module 'language' must be a non-empty string")

        device = config.get("device", "auto")
        if device not in _VALID_DEVICES:
            raise ConfigError(
                f"Pocket module 'device' must be one of {sorted(_VALID_DEVICES)}"
            )

        max_tokens = config.get("max_tokens")
        if max_tokens is not None and (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens <= 0
        ):
            raise ConfigError("Pocket module 'max_tokens' must be an integer > 0")

        try:
            import torch
            from pocket_tts import TTSModel
        except ImportError as exc:
            raise ConfigError(
                "The 'pocket' module requires the pocket extra: "
                "pip install tts-engine[pocket]"
            ) from exc

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            if language is None:
                model = TTSModel.load_model().to(device)
            else:
                model = TTSModel.load_model(language=language).to(device)
            self._voice_state = model.get_state_for_audio_prompt(voice)
        except Exception as exc:
            raise TTSError(f"pocket-tts model load failed: {exc}") from exc

        self._model = model
        self._max_tokens: int | None = max_tokens
        self._sample_rate = int(model.sample_rate)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        max_tokens = self._max_tokens

        def _blocking_stream(stop: threading.Event) -> None:
            # Pass max_tokens only when configured, else the library's own
            # default (50) applies — None would override it and is rejected.
            if max_tokens is None:
                stream_iter = self._model.generate_audio_stream(self._voice_state, text)
            else:
                stream_iter = self._model.generate_audio_stream(
                    self._voice_state, text, max_tokens=max_tokens
                )
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
                callback(_to_int16_pcm(chunk))

        await run_cancellable_worker(_blocking_stream)
