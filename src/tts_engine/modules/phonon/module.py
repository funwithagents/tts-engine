"""Phonon local-model module.

Wraps Gradium's `ptts` runtime directly: a Rust extension running the Phonon
on-device checkpoints in-process, with no torch. `ptts` is imported lazily inside
`__init__` so `import tts_engine` and `load_module` never require the `phonon`
extra — a missing extra becomes a clear `ConfigError`.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

log = logging.getLogger(__name__)

_LANGUAGES = {"en", "fr", "de", "es", "pt", "none"}
_DEFAULT_LANGUAGE = "en"


def _to_int16_pcm(chunk) -> bytes:
    """Convert one float32 chunk (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = np.asarray(chunk, dtype=np.float32).reshape(-1).copy()
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Phonon module '{key}' must be a non-empty string")
    return value


def _optional_number(
    config: dict, key: str, *, minimum: float | None = None
) -> float | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"Phonon module '{key}' must be a number")
    if minimum is not None and value < minimum:
        raise ConfigError(f"Phonon module '{key}' must be >= {minimum}")
    return float(value)


class PhononModule(TTSModule):
    def __init__(self, config: dict) -> None:
        language = config.get("language", _DEFAULT_LANGUAGE)
        if not isinstance(language, str) or language not in _LANGUAGES:
            raise ConfigError(
                f"Phonon module 'language' must be one of {sorted(_LANGUAGES)}"
            )

        model = _optional_str(config, "model")
        if model is not None and not model.startswith("hf://"):
            # A local model directory or config.json: relative to base_dir.
            model = str(resolve_path(config, model))

        voice = _optional_str(config, "voice")
        # A local embedding file is registered under its stem after loading;
        # anything else is a voice name ptts resolves itself.
        voice_file: Path | None = None
        if voice is not None and voice.endswith(".safetensors"):
            voice_file = resolve_path(config, voice)
            voice = voice_file.stem

        device = config.get("device", "auto")
        if not isinstance(device, str) or not device:
            raise ConfigError("Phonon module 'device' must be a non-empty string")

        quant = _optional_str(config, "quant")
        temperature = _optional_number(config, "temperature", minimum=0.0)
        cfg_coef = _optional_number(config, "cfg_coef")

        try:
            import ptts
        except ImportError as exc:
            raise ConfigError(
                "The 'phonon' module requires the phonon extra: "
                "pip install tts-engine[phonon]"
            ) from exc

        # Only the fields that were set: the library's own defaults apply to
        # the rest, and "auto" lets ptts pick its most capable device.
        kwargs: dict = {"lang": None if language == "none" else language}
        if model is not None:
            kwargs["model"] = model
        if device != "auto":
            kwargs["device"] = device
        if quant is not None:
            kwargs["quant"] = quant
        if temperature is not None:
            kwargs["temperature"] = temperature
        if cfg_coef is not None:
            kwargs["cfg_coef"] = cfg_coef
        if voice is not None and voice_file is None:
            # A name: loading it here fails fast on an unknown voice.
            kwargs["voice"] = voice

        try:
            tts = ptts.TTS(**kwargs)
            if voice_file is not None:
                tts.add_voice(voice_file.stem, str(voice_file))
        except (ValueError, NotImplementedError, LookupError, FileNotFoundError) as exc:
            # The library rejected a configured value (language, device,
            # quantization, voice, a missing local file).
            raise ConfigError(f"Phonon module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"phonon model load failed: {exc}") from exc

        self._tts = tts
        self._voice: str | None = voice
        self._sample_rate = int(tts.sample_rate)
        log.info(
            "phonon model loaded: model=%s device=%s quant=%s",
            model or "default",
            getattr(tts, "device", "?"),
            getattr(tts, "quant", "?"),
        )

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one text and feed its PCM, polling `stop`."""
        try:
            audio = self._tts.stream(text, voice=self._voice)
        except Exception as exc:
            raise TTSError(f"phonon synthesis failed: {exc}") from exc
        with audio:  # leaving the block stops the decoder threads, on every path
            while not stop.is_set():
                # Advance inside the provider try so runtime errors become
                # TTSError; feed outside it so a playback failure keeps its
                # own identity.
                try:
                    chunk = next(audio)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"phonon synthesis failed: {exc}") from exc
                callback(_to_int16_pcm(chunk))

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(stop: threading.Event) -> None:
            self._generate(text, callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # ptts streams audio out but needs the whole text in: group the pieces
        # into sentences as they arrive and synthesize each, in one worker.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            for sentence in segment_sentences(pieces):
                self._generate(sentence, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
