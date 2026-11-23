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
from typing import Any

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    cleanup,
    float_to_int16_pcm,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

# The thread count the first Phonon model of this process was loaded with
# (None: ptts's default): ptts sizes its pool once, so later values cannot apply.
# `_pool_sized` says whether a model was loaded yet, since None is also a count.
_pool_sized = False
_pool_threads: int | None = None

_LANGUAGES = ("en", "fr", "de", "es", "pt", "none")
_DEFAULT_LANGUAGE = "en"
# What ptts loads when no model is named (it pins a revision of this one).
_DEFAULT_MODEL = "hf://gradium/phonon-31m-en"
_LABEL = "Phonon module"
_MISSING_EXTRA = (
    "The 'phonon' module requires the phonon extra: pip install tts-engine[phonon]"
)


class PhononModule(TTSModule):
    kind = "local"
    title = "Phonon"
    extra = "phonon"
    fields = (
        Field(
            "language",
            "string",
            "The language the text is normalized for before it is spoken, or "
            "'none' to skip normalization. It does not select the model.",
            default=_DEFAULT_LANGUAGE,
            choices=_LANGUAGES,
            message=f"Phonon module 'language' must be one of {sorted(_LANGUAGES)}",
        ),
        Field(
            "model",
            "string",
            "The model: hf://owner/name[@revision] on the Hugging Face Hub, or "
            "a local model directory.",
            implied=_DEFAULT_MODEL,
        ),
        Field(
            "voice",
            "string",
            "A Phonon catalog voice name such as 'Juniper', or a local "
            ".safetensors voice file. Absent, the model's default voice speaks.",
        ),
        Field(
            "device",
            "string",
            "Where the model runs: 'auto' picks the most capable device of the "
            "installed build, or name one, such as 'cpu' or 'metal'.",
            default="auto",
            advanced=True,
        ),
        Field(
            "quant",
            "string",
            "The weight format to load, such as 'q8_0' or 'q4k'. It works on "
            "the CPU only; absent, the model's own weights are used.",
            advanced=True,
        ),
        Field(
            "temperature",
            "number",
            "The sampling temperature: higher values vary the delivery more.",
            implied=0.3,
            minimum=0.0,
            advanced=True,
        ),
        Field(
            "cfg_coef",
            "number",
            "The guidance coefficient: how closely the speech follows the "
            "voice. Absent, the model's own value is used.",
            advanced=True,
        ),
        Field(
            "threads",
            "integer",
            "The number of CPU threads synthesis runs on. Absent, every core "
            "is used. The first Phonon model loaded in a process decides it.",
            minimum=1,
            advanced=True,
        ),
    )

    @staticmethod
    def _model(values: dict[str, Any], config: dict) -> str | None:
        """The model as ptts takes it: None (its default), an `hf://`
        reference, or a local directory or config.json relative to base_dir."""
        model: str | None = values["model"]
        if model is not None and not model.startswith("hf://"):
            model = str(resolve_path(config, model))
        return model

    @staticmethod
    def _voice(values: dict[str, Any], config: dict) -> tuple[str | None, Path | None]:
        """The voice's name and, for a local embedding file, its path: the
        file is registered under its stem after loading; anything else is a
        voice name ptts resolves itself."""
        voice: str | None = values["voice"]
        if voice is not None and voice.endswith(".safetensors"):
            voice_file = resolve_path(config, voice)
            return voice_file.stem, voice_file
        return voice, None

    @classmethod
    def _load(cls, values: dict[str, Any], config: dict) -> Any:
        """Load the model this config selects (downloading what it lacks) and
        return the `ptts.TTS`, its voice file registered."""
        language: str = values["language"]
        model = cls._model(values, config)
        voice, voice_file = cls._voice(values, config)
        device: str = values["device"]
        quant: str | None = values["quant"]
        temperature: float | None = values["temperature"]
        cfg_coef: float | None = values["cfg_coef"]
        threads: int | None = values["threads"]

        try:
            import ptts
        except ImportError as exc:
            raise ConfigError(_MISSING_EXTRA) from exc

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

        global _pool_sized, _pool_threads
        if threads is not None:
            # Sizes ptts's process-global pool, built once at the first model
            # load: the first Phonon model of a process decides for the others,
            # and a later value is accepted by ptts but has no effect.
            try:
                ptts.set_num_threads(threads)
            except Exception as exc:
                raise ConfigError(f"Phonon module 'threads': {exc}") from exc
        if _pool_sized and _pool_threads != threads:
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

        if not _pool_sized:
            _pool_sized, _pool_threads = True, threads
        return tts

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        """The model and, when one is named, the voice. ptts downloads inside
        the model load and picks the files itself (by device, weight format
        and what the repository holds), so what comes from the Hub is
        `unknown` until loaded; a local path is `ready` or `missing`."""
        values = cls.validated(config)
        model = cls._model(values, config)
        voice, voice_file = cls._voice(values, config)
        try:
            import ptts  # noqa: F401  (only whether the extra is there)

            installed = True
        except ImportError:
            installed = False

        def local(path: Path) -> dict[str, Any]:
            present = path.exists()
            return {
                "status": "ready" if present else "missing",
                "detail": str(path) if present else f"Not found: {path}",
                "preparable": False,
            }

        def fetched(detail: str) -> dict[str, Any]:
            return {
                "status": "unknown",
                "detail": detail if installed else "The phonon extra is not installed.",
                "preparable": True,
            }

        found: list[dict[str, Any]] = []
        item: dict[str, Any] = {"id": "phonon:model", "title": "Phonon model"}
        if model is not None and not model.startswith("hf://"):
            item |= local(Path(model))
        else:
            item |= fetched(
                f"{model or _DEFAULT_MODEL} is fetched from the Hugging Face "
                "Hub the first time it loads."
            )
        found.append(item)
        if voice is not None:
            item = {"id": "phonon:voice", "title": "Phonon voice"}
            if voice_file is not None:
                item |= local(voice_file)
            else:
                item |= fetched(
                    f"The voice {voice!r} is fetched from the Phonon voice "
                    "catalog the first time the model loads with it."
                )
            found.append(item)
        return found

    @classmethod
    def prepare(
        cls,
        config: dict,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        # ptts has no download of its own apart from the load: load the model
        # as the constructor does, then release it.
        pending = [
            item
            for item in cls.check_files(config)
            if item["status"] != "ready" and item["preparable"]
        ]
        if not pending:
            return
        if on_progress is not None:
            for item in pending:
                on_progress(
                    {
                        "id": item["id"],
                        "fraction": None,
                        "message": "Loading the model to fetch its files",
                    }
                )
        cls._load(cls.validated(config), config)
        if on_progress is not None:
            for item in pending:
                on_progress(
                    {
                        "id": item["id"],
                        "fraction": 1.0,
                        "message": f"{item['title']} ready",
                    }
                )

    @classmethod
    def imports(cls) -> None:
        try:
            import ptts  # noqa: F401
        except ImportError as exc:
            raise ConfigError(_MISSING_EXTRA) from exc

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        tts = self._load(values, config)
        self._tts = tts
        self._voice_name: str | None = self._voice(values, config)[0]
        self._sample_rate = int(tts.sample_rate)
        log.info(
            "phonon model loaded: model=%s device=%s quant=%s threads=%s",
            self._model(values, config) or "default",
            getattr(tts, "device", "?"),
            getattr(tts, "quant", "?"),
            values["threads"] or "default",
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
            raise ConfigError(_MISSING_EXTRA) from exc
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
            audio = self._tts.stream(text, voice=self._voice_name)
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
