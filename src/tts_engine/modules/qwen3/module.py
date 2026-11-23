"""Qwen3-TTS local-model module.

Runs Qwen3-TTS in process through `qwentts-cpp-python`, ctypes bindings over
the qwentts.cpp GGML engine (Metal on Apple silicon, CUDA on Linux), with GGUF
weights from the Hugging Face Hub. `qwentts_cpp` is imported lazily inside
`__init__` so `import tts_engine` and `load_module` never require the `qwen3`
extra — a missing extra becomes a clear `ConfigError`.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable, Generator, Iterable, Iterator
from typing import Any, cast

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    VoiceListingUnsupported,
    cleanup,
    float_to_int16_pcm,
    optional_str,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_DEFAULT_QUANT = "Q8_0"
_DEFAULT_VOICE = "aiden"
_DEFAULT_LANGUAGE = "auto"
# The 12 Hz codec decodes 1920-sample frames at 24 kHz on every model.
_SAMPLE_RATE = 24000
_LABEL = "Qwen3 module"
_MISSING_EXTRA = (
    "The 'qwen3' module requires the qwen3 extra: "
    "pip install tts-engine[qwen3] (Apple-silicon macOS; on Linux "
    "with CUDA 12.8, pip install qwentts-cpp-python)"
)


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
    kind = "local"
    title = "Qwen3"
    extra = "qwen3"
    fields = (
        Field(
            "model",
            "string",
            "The Qwen3-TTS model: a -CustomVoice model speaks with preset "
            "voices, the -VoiceDesign model with a voice described in 'instruct'.",
            implied=_DEFAULT_MODEL,
        ),
        Field(
            "quant",
            "string",
            "The weight format to download and load: Q8_0, Q4_K_M, BF16 or F32.",
            implied=_DEFAULT_QUANT,
            advanced=True,
        ),
        Field(
            "voice",
            "string",
            "A preset voice of a CustomVoice model, such as 'aiden' or 'ryan'. "
            "Not allowed with a VoiceDesign model.",
            implied=_DEFAULT_VOICE,
        ),
        Field(
            "instruct",
            "string",
            "A style instruction for a CustomVoice model ('Speak cheerfully.'), "
            "or the description of the voice for a VoiceDesign model, which "
            "requires it.",
        ),
        Field(
            "language",
            "string",
            "The language of the text, such as 'english' or 'french', or "
            "'auto' to let the model detect it.",
            implied=_DEFAULT_LANGUAGE,
        ),
        Field(
            "temperature",
            "number",
            "The sampling temperature: higher values vary the delivery more.",
            implied=0.9,
            exclusive_minimum=0,
            advanced=True,
        ),
    )

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        # The model kind decides which voice fields apply; read it from the id
        # so a bad combination fails before a multi-gigabyte download.
        model: str = values["model"] or _DEFAULT_MODEL
        if model.endswith("-Base"):
            raise ConfigError(
                "Qwen3 module 'model': the -Base models need voice cloning, which "
                "is not supported; use a -CustomVoice or -VoiceDesign model"
            )
        if model.endswith("-VoiceDesign"):
            if values["instruct"] is None:
                raise ConfigError(
                    "Qwen3 module 'instruct' is required for a VoiceDesign model "
                    "(it describes the voice)"
                )
            if values["voice"] is not None:
                raise ConfigError(
                    "Qwen3 module 'voice' is only valid for a CustomVoice model"
                )

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        """The two GGUF files the model loads from (its talker, and the codec
        of its weight format), looked up in the Hugging Face cache."""
        values = cls.validated(config)
        model: str = values["model"] or _DEFAULT_MODEL
        quant: str = values["quant"] or _DEFAULT_QUANT
        titles = {"talker": "Qwen3 model file", "codec": "Qwen3 codec file"}
        try:
            import qwentts_cpp  # pyright: ignore[reportMissingImports]
        except ImportError:
            return [
                {
                    "id": "qwen3:model",
                    "title": "Qwen3 model",
                    "status": "unknown",
                    "detail": "The qwen3 extra is not installed.",
                    "preparable": True,
                }
            ]
        try:
            # The library's own lookup, kept off the network: both paths when
            # both files are cached, and its message for a model id or a
            # weight format it does not know.
            paths = qwentts_cpp.resolve_gguf_paths(
                model, quant=quant, local_files_only=True
            )
        except ValueError as exc:
            raise ConfigError(f"Qwen3 module: {exc}") from exc
        except Exception:  # noqa: BLE001  (not cached: say which file below)
            paths = None
        if paths is not None:
            return [
                {
                    "id": f"qwen3:{key}",
                    "title": titles[key],
                    "status": "ready",
                    "detail": str(path),
                    "preparable": True,
                }
                for key, path in zip(titles, paths, strict=True)
            ]
        try:
            # The file names the library derives, to tell which one is missing.
            from huggingface_hub import try_to_load_from_cache

            library = qwentts_cpp.models
            name = library._normalize_quant(quant)
            filenames = {
                "talker": f"{library._MODEL_TO_TALKER_STEM[model]}-{name}.gguf",
                "codec": f"qwen-tokenizer-12hz-{name}.gguf",
            }
            repo_id: str = library.GGUF_REPO
        except (ImportError, AttributeError, KeyError):
            return [
                {
                    "id": "qwen3:model",
                    "title": "Qwen3 model",
                    "status": "missing",
                    "detail": f"The {quant} weights of {model} are not downloaded yet.",
                    "preparable": True,
                }
            ]
        found = []
        for key, filename in filenames.items():
            cached = try_to_load_from_cache(repo_id, filename)
            present = isinstance(cached, str)
            found.append(
                {
                    "id": f"qwen3:{key}",
                    "title": titles[key],
                    "status": "ready" if present else "missing",
                    "detail": (
                        cached
                        if present
                        else f"{filename} of {repo_id} is not downloaded yet."
                    ),
                    "preparable": True,
                }
            )
        return found

    @classmethod
    def prepare(
        cls,
        config: dict,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        pending = [
            item for item in cls.check_files(config) if item["status"] != "ready"
        ]
        if not pending:
            return
        values = cls.validated(config)
        model: str = values["model"] or _DEFAULT_MODEL
        quant: str = values["quant"] or _DEFAULT_QUANT
        if on_progress is not None:
            for item in pending:
                on_progress(
                    {
                        "id": item["id"],
                        "fraction": None,
                        "message": f"Downloading the {item['title']}",
                    }
                )
        try:
            import qwentts_cpp  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ConfigError(_MISSING_EXTRA) from exc
        try:
            # The download `from_pretrained` makes, without the load.
            qwentts_cpp.resolve_gguf_paths(model, quant=quant)
        except ValueError as exc:
            raise ConfigError(f"Qwen3 module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"qwen3 download failed: {exc}") from exc
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
            import qwentts_cpp  # pyright: ignore[reportMissingImports]  # noqa: F401
        except ImportError as exc:
            raise ConfigError(_MISSING_EXTRA) from exc

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        model: str = values["model"] or _DEFAULT_MODEL
        quant: str = values["quant"] or _DEFAULT_QUANT
        voice: str | None = values["voice"]
        instruct: str | None = values["instruct"]
        language: str = values["language"] or _DEFAULT_LANGUAGE
        temperature: float | None = values["temperature"]
        if voice is None and not model.endswith("-VoiceDesign"):
            voice = _DEFAULT_VOICE

        self.imports()
        import qwentts_cpp  # pyright: ignore[reportMissingImports]

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
            voice_kwargs["temperature"] = temperature

        self._tts = tts
        self._language = language
        self._voice_kwargs = voice_kwargs
        log.info(
            "qwen3 model loaded: model=%s quant=%s type=%s",
            model,
            quant,
            tts.model_type(),
        )

    def close(self) -> None:
        # Free the native context (and GPU memory) now, then drop the runtime;
        # the module is done.
        tts = vars(self).pop("_tts", None)
        if tts is not None:
            tts.close()

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The preset speakers of the configured CustomVoice model (`model`
        and `quant`, defaulted as for construction), read from the loaded
        model, which is closed again: this downloads and loads the weights. A
        VoiceDesign model has no presets, its voice is the `instruct` text."""
        model = optional_str(config, "model", label=_LABEL) or _DEFAULT_MODEL
        quant = optional_str(config, "quant", label=_LABEL) or _DEFAULT_QUANT
        if model.endswith("-Base"):
            raise ConfigError(
                "Qwen3 module 'model': the -Base models need voice cloning, which "
                "is not supported; use a -CustomVoice or -VoiceDesign model"
            )
        if model.endswith("-VoiceDesign"):
            raise VoiceListingUnsupported(
                f"Qwen3 module: {model} has no preset voices; describe the voice "
                "in 'instruct' (e.g. \"A calm, warm male narrator.\")"
            )
        try:
            import qwentts_cpp  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ConfigError(_MISSING_EXTRA) from exc
        try:
            tts = qwentts_cpp.QwenTTS.from_pretrained(
                model, quant=quant, log_level="warning"
            )
        except ValueError as exc:
            raise ConfigError(f"Qwen3 module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"qwen3 model load failed: {exc}") from exc
        try:
            names = tts.speaker_names()
        except Exception as exc:
            raise TTSError(f"{_LABEL}: voice listing failed: {exc}") from exc
        finally:
            tts.close()
        return [VoiceInfo(id=name, name=name, config={"voice": name}) for name in names]

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
                callback(float_to_int16_pcm(chunk))

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
