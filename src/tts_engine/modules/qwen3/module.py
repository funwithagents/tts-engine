"""Qwen3-TTS local-model module.

Runs Qwen3-TTS in process through `qwentts-cpp-python`, ctypes bindings over
the qwentts.cpp GGML engine (Metal on Apple silicon, CUDA on Linux), with GGUF
weights from the Hugging Face Hub. `qwentts_cpp` is imported lazily inside
`__init__` so `import tts_engine` and `load_module` never require the `qwen3`
extra — a missing extra becomes a clear `ConfigError`.
"""

import logging
import math
import threading
from collections.abc import AsyncIterator, Callable, Generator, Iterable, Iterator
from typing import cast

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    cleanup,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_DEFAULT_QUANT = "Q8_0"
_DEFAULT_VOICE = "aiden"
_DEFAULT_LANGUAGE = "auto"
# The 12 Hz codec decodes 1920-sample frames at 24 kHz on every model.
_SAMPLE_RATE = 24000


def _to_int16_pcm(chunk) -> bytes:
    """Convert one float32 chunk (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = np.asarray(chunk, dtype=np.float32).reshape(-1).copy()
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Qwen3 module '{key}' must be a non-empty string")
    return value


def _match(value: str, choices: Iterable[str], field: str) -> str:
    """The canonical spelling of `value` among the loaded model's `choices`,
    compared case-insensitively; ConfigError listing them on a miss."""
    by_lower = {choice.lower(): choice for choice in choices}
    match = by_lower.get(value.lower())
    if match is None:
        raise ConfigError(
            f"Qwen3 module '{field}' must be one of {sorted(by_lower.values())}, "
            f"got {value!r}"
        )
    return match


class Qwen3Module(TTSModule):
    def __init__(self, config: dict) -> None:
        model = _optional_str(config, "model") or _DEFAULT_MODEL
        quant = _optional_str(config, "quant") or _DEFAULT_QUANT
        voice = _optional_str(config, "voice")
        instruct = _optional_str(config, "instruct")
        language = _optional_str(config, "language") or _DEFAULT_LANGUAGE

        temperature = config.get("temperature")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or not math.isfinite(temperature)
            or temperature <= 0
        ):
            raise ConfigError("Qwen3 module 'temperature' must be a finite number > 0")

        # The model kind decides which voice fields apply; read it from the id
        # so a bad combination fails before a multi-gigabyte download.
        if model.endswith("-Base"):
            raise ConfigError(
                "Qwen3 module 'model': the -Base models need voice cloning, which "
                "is not supported; use a -CustomVoice or -VoiceDesign model"
            )
        voice_design = model.endswith("-VoiceDesign")
        if voice_design:
            if instruct is None:
                raise ConfigError(
                    "Qwen3 module 'instruct' is required for a VoiceDesign model "
                    "(it describes the voice)"
                )
            if voice is not None:
                raise ConfigError(
                    "Qwen3 module 'voice' is only valid for a CustomVoice model"
                )
        elif voice is None:
            voice = _DEFAULT_VOICE

        try:
            import qwentts_cpp  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ConfigError(
                "The 'qwen3' module requires the qwen3 extra: "
                "pip install tts-engine[qwen3] (Apple-silicon macOS; on Linux "
                "with CUDA 12.8, pip install qwentts-cpp-python)"
            ) from exc

        try:
            # warning: without it the native engine prints per-frame progress
            # to stderr (the filter is process-wide).
            tts = qwentts_cpp.QwenTTS.from_pretrained(
                model, quant=quant, log_level="warning"
            )
        except ValueError as exc:
            # Unknown model id or quant, rejected before any download.
            raise ConfigError(f"Qwen3 module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"qwen3 model load failed: {exc}") from exc

        try:
            # The library only rejects an unknown speaker on the first
            # synthesis, with a bare status code: check both here.
            if voice is not None:
                voice = _match(voice, tts.speaker_names(), "voice")
            language = _match(
                language, ["auto", *tts.language_names()], "language"
            ).lower()
        except ConfigError:
            tts.close()  # free the native context (and GPU memory) now
            raise

        voice_kwargs: dict = {}
        if voice is not None:
            voice_kwargs["speaker"] = voice
        if instruct is not None:
            voice_kwargs["instruct"] = instruct
        if temperature is not None:
            voice_kwargs["temperature"] = float(temperature)

        self._tts = tts
        self._language = language
        self._voice_kwargs = voice_kwargs
        log.info(
            "qwen3 model loaded: model=%s quant=%s type=%s",
            model,
            quant,
            tts.model_type(),
        )

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one sentence and feed its PCM, polling `stop`."""
        # Annotated as an Iterator upstream, but a generator: closing it cancels
        # the native synthesis and joins its producer thread, on every path.
        chunks = cast(
            Generator,
            self._tts.stream(text=text, lang=self._language, **self._voice_kwargs),
        )
        # A failing close never hides the utterance's own error.
        with cleanup(chunks.close, "qwen3 stream"):
            while not stop.is_set():
                # Advance inside the provider try so runtime errors become
                # TTSError; feed outside it so a playback failure keeps its
                # own identity.
                try:
                    chunk, rate = next(chunks)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"qwen3 synthesis failed: {exc}") from exc
                if rate != _SAMPLE_RATE:
                    raise TTSError(
                        f"qwen3 produced {rate} Hz audio, expected {_SAMPLE_RATE} Hz"
                    )
                callback(_to_int16_pcm(chunk))

    def _generate_sentences(
        self,
        sentences: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        for sentence in sentences:
            if stop.is_set():
                return
            self._generate(sentence, callback, stop)

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # A sentence per call even for a complete text: keeps each call far
        # from the per-call frame cap and away from long-prompt drift.
        def _worker(stop: threading.Event) -> None:
            self._generate_sentences(segment_sentences([text]), callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # qwentts.cpp needs the whole prompt per call: group the pieces into
        # sentences as they arrive and synthesize each, in one worker.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._generate_sentences(segment_sentences(pieces), callback, stop)

        await run_text_stream_worker(text_stream, _worker)
