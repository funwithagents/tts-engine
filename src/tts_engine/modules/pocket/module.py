"""Pocket TTS local-model module.

Wraps kyutai-labs/pocket-tts directly (not the huggingface/speech-to-speech
handler): a family of small models that run in-process. `torch` and `pocket_tts`
are imported lazily inside `__init__` so `import tts_engine` and `load_module`
never require the `pocket` extra — a missing extra becomes a clear `ConfigError`.
"""

import logging
import math
import threading
from collections.abc import AsyncIterator, Callable, Iterator

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

_VALID_DEVICES = {"auto", "cpu", "cuda", "mps"}
_VOICE_FILE_SUFFIXES = (".wav", ".mp3", ".flac", ".safetensors")
_URL_PREFIXES = ("hf://", "http://", "https://")


def _to_int16_pcm(chunk) -> bytes:
    """Convert one 1-D float32 torch tensor (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = chunk.detach().to("cpu").numpy().reshape(-1).astype(np.float32)
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


def _optional_string(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Pocket module '{key}' must be a non-empty string")
    return value


class PocketModule(TTSModule):
    def __init__(self, config: dict) -> None:
        voice = config.get("voice")
        if "voice" in config and (not isinstance(voice, str) or not voice):
            raise ConfigError("Pocket module 'voice' must be a non-empty string")
        if (
            voice is not None
            and voice.endswith(_VOICE_FILE_SUFFIXES)
            and "://" not in voice
        ):
            # A local voice file: relative to the config file (base_dir).
            voice = str(resolve_path(config, voice))

        language = _optional_string(config, "language")
        model_config = _optional_string(config, "config")
        if language is not None and model_config is not None:
            raise ConfigError("Pocket module accepts 'language' or 'config', not both")
        if model_config is not None and not model_config.startswith(_URL_PREFIXES):
            model_config = str(resolve_path(config, model_config))

        device = config.get("device", "auto")
        if not isinstance(device, str) or device not in _VALID_DEVICES:
            raise ConfigError(
                f"Pocket module 'device' must be one of {sorted(_VALID_DEVICES)}"
            )

        quantize = config.get("quantize", False)
        if not isinstance(quantize, bool):
            raise ConfigError("Pocket module 'quantize' must be a boolean")
        if quantize and device in ("cuda", "mps"):
            raise ConfigError("Pocket module 'quantize' is CPU-only")

        temperature = config.get("temperature")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or not math.isfinite(temperature)
            or temperature < 0
        ):
            raise ConfigError(
                "Pocket module 'temperature' must be a finite number >= 0"
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
            from pocket_tts.default_parameters import get_default_voice_for_language
        except ImportError as exc:
            raise ConfigError(
                "The 'pocket' module requires the pocket extra: "
                "pip install tts-engine[pocket]"
            ) from exc

        if device == "auto":
            if quantize:
                device = "cpu"
            else:
                device = "cuda" if torch.cuda.is_available() else "cpu"

        # Only the set fields, so the library's own defaults apply otherwise.
        load_kwargs: dict = {}
        if language is not None:
            load_kwargs["language"] = language
        if model_config is not None:
            load_kwargs["config"] = model_config
        if temperature is not None:
            load_kwargs["temp"] = float(temperature)
        if quantize:
            load_kwargs["quantize"] = True
        if voice is None:
            voice = get_default_voice_for_language(language, model_config)

        try:
            model = TTSModel.load_model(**load_kwargs).to(device)
            self._voice_state = model.get_state_for_audio_prompt(voice)
        except (ValueError, FileNotFoundError) as exc:
            # The library rejecting a value: unknown language, a non-YAML
            # config, a missing file, a preset voice with custom weights, an
            # audio voice without the gated cloning weights.
            raise ConfigError(f"Pocket module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"pocket-tts model load failed: {exc}") from exc

        log.info(
            "pocket-tts loaded: model=%s device=%s quantize=%s",
            language or model_config or "default",
            device,
            quantize,
        )
        self._model = model
        self._max_tokens: int | None = max_tokens
        self._sample_rate = int(model.sample_rate)

    def close(self) -> None:
        # Drop the model and the voice state so their tensors can be
        # collected; the module is done.
        vars(self).pop("_model", None)
        vars(self).pop("_voice_state", None)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Run inference for one text and feed its PCM until done or `stop`."""
        # The library takes the stop event itself, so cancellation also halts
        # its generation thread mid-sentence.
        kwargs: dict = {"stop": stop}
        if self._max_tokens is not None:
            # Only when configured, else the library's own default (50) applies.
            kwargs["max_tokens"] = self._max_tokens
        stream_iter = self._model.generate_audio_stream(
            self._voice_state, text, **kwargs
        )
        # Still poll `stop`: the decoder may hand back frames it already queued.
        try:
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
        except BaseException:
            # The utterance is over (a sink failure, a synthesis error): tell
            # the library's generation thread to stop now, as a cancel does,
            # instead of letting it run the sentence to its end in the
            # background while the model may be reused.
            stop.set()
            raise
        finally:
            self._settle(stream_iter)

    @staticmethod
    def _settle(stream_iter: Iterator) -> None:
        """Run the library's generator to its end without feeding anything.

        After `stop` (a cancel, a sink failure) the loop above leaves the
        generator suspended at a yield. The library joins its decoder thread
        only when its generator runs to completion, so an abandoned generator
        would leave that thread decoding the frames it had queued while the
        model is reused for the next utterance (generation is documented as
        not thread-safe). With `stop` set the library produces no new frames,
        so draining is bounded by what was already queued. A library error
        while draining is logged: the utterance's outcome is already decided.
        """
        try:
            for _ in stream_iter:
                pass
        except Exception:
            log.warning("pocket-tts generator failed while draining", exc_info=True)

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
        # The model conditions on its whole prompt, and the library itself
        # splits a long text into sentences: do that split as the text arrives
        # and run inference per sentence, inside one worker.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            for sentence in segment_sentences(pieces):
                self._generate(sentence, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
