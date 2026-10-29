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

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    cleanup,
    finite_number,
    float_to_int16_pcm,
    optional_str,
    positive_int,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

log = logging.getLogger(__name__)

# The thread count the first Phonon model of this process was loaded with
# (None: ptts's default): ptts sizes its pool once, so later values cannot apply.
_pool_threads: int | None = None

_LANGUAGES = {"en", "fr", "de", "es", "pt", "none"}
_DEFAULT_LANGUAGE = "en"
_LABEL = "Phonon module"


class PhononModule(TTSModule):
    kind = "local"

    def __init__(self, config: dict) -> None:
        language = config.get("language", _DEFAULT_LANGUAGE)
        if not isinstance(language, str) or language not in _LANGUAGES:
            raise ConfigError(
                f"Phonon module 'language' must be one of {sorted(_LANGUAGES)}"
            )

        model = optional_str(config, "model", label=_LABEL)
        if model is not None and not model.startswith("hf://"):
            # A local model directory or config.json: relative to base_dir.
            model = str(resolve_path(config, model))

        voice = optional_str(config, "voice", label=_LABEL)
        # A local embedding file is registered under its stem after loading;
        # anything else is a voice name ptts resolves itself.
        voice_file: Path | None = None
        if voice is not None and voice.endswith(".safetensors"):
            voice_file = resolve_path(config, voice)
            voice = voice_file.stem

        device = config.get("device", "auto")
        if not isinstance(device, str) or not device:
            raise ConfigError("Phonon module 'device' must be a non-empty string")

        quant = optional_str(config, "quant", label=_LABEL)
        temperature = finite_number(config, "temperature", label=_LABEL, minimum=0.0)
        cfg_coef = finite_number(config, "cfg_coef", label=_LABEL)
        threads = positive_int(config, "threads", label=_LABEL)

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

        global _pool_threads
        if threads is not None:
            # Sizes ptts's process-global pool, built once at the first model
            # load: the first Phonon model of a process decides for the others,
            # and a later value is accepted by ptts but has no effect.
            try:
                ptts.set_num_threads(threads)
            except Exception as exc:
                raise ConfigError(f"Phonon module 'threads': {exc}") from exc
        if _pool_threads is not None and _pool_threads != threads:
            log.warning(
                "Phonon module 'threads' %s cannot apply: a model already loaded "
                "in this process sized the thread pool (threads=%s)",
                threads or "default",
                _pool_threads or "default",
            )

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

        if _pool_threads is None:
            _pool_threads = threads
        self._tts = tts
        self._voice: str | None = voice
        self._sample_rate = int(tts.sample_rate)
        log.info(
            "phonon model loaded: model=%s device=%s quant=%s threads=%s",
            model or "default",
            getattr(tts, "device", "?"),
            getattr(tts, "quant", "?"),
            threads or "default",
        )

    def close(self) -> None:
        # Drop the runtime so its weights can be collected; the module is done.
        vars(self).pop("_tts", None)

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The voice catalog `ptts` pins (`ptts.catalog_voices()`), in every
        language: the catalog is shared by the checkpoints and has no gender
        field. Only the catalog index is downloaded, no voice and no model."""
        try:
            import ptts
        except ImportError as exc:
            raise ConfigError(
                "The 'phonon' module requires the phonon extra: pip install tts-engine[phonon]"
            ) from exc
        try:
            entries = ptts.catalog_voices()
        except Exception as exc:
            raise TTSError(f"{_LABEL}: voice listing failed: {exc}") from exc
        return [
            VoiceInfo(
                id=str(entry["name"]),
                name=str(entry["name"]),
                config={"voice": entry["name"]},
                languages=(str(entry["language"]),) if entry.get("language") else (),
                description=entry.get("description") or None,
            )
            for entry in entries
            if entry.get("name")
        ]

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
        # Leaving the block stops the decoder threads, on every path; a failing
        # close never hides the utterance's own error.
        with cleanup(lambda: audio.__exit__(None, None, None), "phonon stream"):
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
                callback(float_to_int16_pcm(chunk))

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
