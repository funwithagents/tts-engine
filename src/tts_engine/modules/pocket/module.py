"""Pocket TTS local-model module.

Wraps kyutai-labs/pocket-tts directly (not the huggingface/speech-to-speech
handler): a family of small models that run in-process. `torch` and `pocket_tts`
are imported lazily inside `__init__` so `import tts_engine` and `load_module`
never require the `pocket` extra — a missing extra becomes a clear `ConfigError`.
"""

import hashlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    VoiceListingUnsupported,
    float_to_int16_pcm,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

_VALID_DEVICES = {"auto", "cpu", "cuda", "mps"}
_VOICE_FILE_SUFFIXES = (".wav", ".mp3", ".flac", ".safetensors")
_URL_PREFIXES = ("hf://", "http://", "https://")
_LABEL = "Pocket module"
_ITEM_TITLES = {
    "model": "Pocket model weights",
    "tokenizer": "Pocket tokenizer",
    "voice": "Pocket voice",
}


def _cached(source: str) -> tuple[str | None, bool]:
    """Where pocket-tts's `download_if_necessary` would find `source` without
    fetching it (None when it would fetch, or when a local file is absent),
    and whether it can be fetched. Mirrors that function: an `hf://` reference
    is in the Hugging Face cache, an `http(s)://` one in `~/.cache/pocket_tts`
    under the hash of its URL, anything else is a local path."""
    if source.startswith(("http://", "https://")):
        suffix = Path(urlparse(source).path).suffix
        name = hashlib.sha256(source.encode()).hexdigest() + suffix
        path = Path.home() / ".cache" / "pocket_tts" / name
        return (str(path) if path.exists() else None), True
    if source.startswith("hf://"):
        from huggingface_hub import try_to_load_from_cache

        parts = source.removeprefix("hf://").split("/")
        repo_id, filename, revision = "/".join(parts[:2]), "/".join(parts[2:]), None
        if "@" in filename:
            filename, revision = filename.split("@")
        cached = try_to_load_from_cache(repo_id, filename, revision=revision)
        return (cached if isinstance(cached, str) else None), True
    return (source if Path(source).is_file() else None), False


def _is_voice_state(voice: str) -> bool:
    """Whether `voice` is a `.safetensors` voice state (the library's rule)."""
    if voice.startswith(("http://", "https://")):
        voice = urlparse(voice).path
    elif voice.startswith("hf://"):
        voice = voice.rsplit("@", 1)[0]
    return voice.endswith(".safetensors")


class PocketModule(TTSModule):
    kind = "local"
    title = "Pocket"
    extra = "pocket"
    fields = (
        Field(
            "voice",
            "string",
            "A preset voice name such as 'alba', an audio file to clone "
            "(.wav, .mp3, .flac) or a .safetensors voice state, as a local "
            "path or an hf:// or https:// URL. Absent, the model's default voice.",
            nullable=False,
        ),
        Field(
            "language",
            "string",
            "The model to load, named by its language: 'english', 'french', "
            "'german', 'italian', 'portuguese', 'spanish', 'dutch', or a "
            "variant such as 'english_2m_2026-10'. Not together with 'config'.",
            implied="english",
        ),
        Field(
            "config",
            "string",
            "A custom model config YAML, for community or fine-tuned weights: "
            "a local path or an hf:// or https:// URL. Not together with "
            "'language'.",
            advanced=True,
        ),
        Field(
            "device",
            "string",
            "The device the model runs on. 'auto' picks CUDA when available, "
            "else the CPU.",
            default="auto",
            choices=tuple(sorted(_VALID_DEVICES)),
            message=f"Pocket module 'device' must be one of {sorted(_VALID_DEVICES)}",
        ),
        Field(
            "quantize",
            "boolean",
            "Run the model with 8-bit weights: about half the memory, and "
            "faster on x86 CPUs. CPU only.",
            default=False,
            advanced=True,
        ),
        Field(
            "temperature",
            "number",
            "The sampling temperature: higher values vary the speech more.",
            implied=0.3,
            minimum=0.0,
            advanced=True,
        ),
        Field(
            "max_tokens",
            "integer",
            "The token cap per chunk of text. Raise it only for unusually "
            "long unbroken text.",
            implied=50,
            exclusive_minimum=0,
            advanced=True,
        ),
    )

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        if values["language"] is not None and values["config"] is not None:
            raise ConfigError("Pocket module accepts 'language' or 'config', not both")
        if values["quantize"] and values["device"] in ("cuda", "mps"):
            raise ConfigError("Pocket module 'quantize' is CPU-only")

    @staticmethod
    def _voice(values: dict[str, Any], config: dict) -> str | None:
        """The configured voice as the library takes it: a local voice file
        relative to the config file (base_dir), anything else untouched."""
        voice: str | None = values["voice"]
        if (
            voice is not None
            and voice.endswith(_VOICE_FILE_SUFFIXES)
            and "://" not in voice
        ):
            return str(resolve_path(config, voice))
        return voice

    @classmethod
    def _sources(cls, config: dict) -> dict[str, list[str]] | None:
        """What loading this config reads, by item key: for each, the sources
        the library tries in order, the first available one being used. None
        when that cannot be told without loading the model (a custom `config`,
        an unknown `language`, a library laid out differently). Imports the
        library, reads its packaged model config, fetches nothing."""
        values = cls.validated(config)
        from pocket_tts import default_parameters
        from pocket_tts.utils import utils as pocket_utils
        from pocket_tts.utils.config import CONFIGS_DIR, load_config

        if values["config"] is not None:
            return None
        try:
            language = values["language"] or default_parameters.DEFAULT_LANGUAGE
            path = CONFIGS_DIR / f"{language}.yaml"
            if not path.is_file():
                return None
            model = load_config(path)
            weights = model.weights_path
            public_weights = model.weights_path_without_voice_cloning
            tokenizer = model.flow_lm.lookup_table.tokenizer_path
            if (
                not isinstance(weights, str)
                or not isinstance(tokenizer, str)
                or model.flow_lm.weights_path is not None
                or model.mimi.weights_path is not None
            ):
                return None
            voice = cls._voice(
                values, config
            ) or default_parameters.get_default_voice_for_language(
                values["language"], None
            )
            cloned = False
            if _is_voice_state(voice):
                pass
            elif voice in pocket_utils._ORIGINS_OF_PREDEFINED_VOICES:
                # A preset is the voice state precomputed for this model.
                voice = pocket_utils.get_predefined_voice(
                    language=path.stem, name=voice
                )
            else:
                cloned = True
            # Cloning needs the gated weights; otherwise the library falls
            # back on the public ones when it cannot get those.
            models = [weights]
            if isinstance(public_weights, str) and not cloned:
                models.append(public_weights)
            return {
                "model": models,
                "tokenizer": [tokenizer],
                "voice": [voice],
            }
        except Exception:
            log.debug("pocket-tts files could not be identified", exc_info=True)
            return None

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        unknown = {
            "id": "pocket:model",
            "title": "Pocket model",
            "status": "unknown",
            "preparable": True,
        }
        try:
            sources = cls._sources(config)
        except ImportError:
            return [unknown | {"detail": "The pocket extra is not installed."}]
        if sources is None:
            return [
                unknown
                | {
                    "detail": "The model and its voice are fetched the first "
                    "time the model loads."
                }
            ]
        found = []
        for key, candidates in sources.items():
            looked_up = [_cached(source) for source in candidates]
            cached = next((path for path, _ in looked_up if path is not None), None)
            preparable = looked_up[0][1]
            if cached is not None:
                detail = cached
            elif preparable:
                detail = f"{candidates[0]} is not downloaded yet."
            else:
                detail = f"File not found: {candidates[0]}"
            found.append(
                {
                    "id": f"pocket:{key}",
                    "title": _ITEM_TITLES[key],
                    "status": "ready" if cached is not None else "missing",
                    "detail": detail,
                    "preparable": preparable,
                }
            )
        return found

    @classmethod
    def prepare(
        cls,
        config: dict,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        try:
            sources = cls._sources(config)
            from pocket_tts.utils.utils import download_if_necessary
        except ImportError as exc:
            raise ConfigError(
                "The 'pocket' module requires the pocket extra: "
                "pip install tts-engine[pocket]"
            ) from exc

        def report(item_id: str, fraction: float | None, message: str) -> None:
            if on_progress is not None:
                on_progress({"id": item_id, "fraction": fraction, "message": message})

        if sources is None:
            # The files cannot be named ahead: load the model once, which
            # fetches them, and release it.
            report("pocket:model", None, "Loading the model to fetch its files")
            cls(config).close()
            report("pocket:model", 1.0, "Model ready")
            return
        for item in cls.check_files(config):
            if item["status"] == "ready" or not item["preparable"]:
                continue
            candidates = sources[item["id"].split(":", 1)[1]]
            report(item["id"], None, f"Downloading {item['title']}")
            for index, source in enumerate(candidates):
                try:
                    # The call the model load makes for each of its files.
                    download_if_necessary(source)
                    break
                except Exception as exc:
                    if index == len(candidates) - 1:
                        raise TTSError(f"pocket-tts download failed: {exc}") from exc
            report(item["id"], 1.0, f"{item['title']} ready")

    @classmethod
    def imports(cls) -> None:
        try:
            import torch  # noqa: F401
            from pocket_tts import TTSModel  # noqa: F401
            from pocket_tts.default_parameters import (
                get_default_voice_for_language,  # noqa: F401
            )
        except ImportError as exc:
            raise ConfigError(
                "The 'pocket' module requires the pocket extra: "
                "pip install tts-engine[pocket]"
            ) from exc

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        voice = self._voice(values, config)
        language: str | None = values["language"]
        model_config: str | None = values["config"]
        if model_config is not None and not model_config.startswith(_URL_PREFIXES):
            model_config = str(resolve_path(config, model_config))
        device: str = values["device"]
        quantize: bool = values["quantize"]
        temperature: float | None = values["temperature"]
        max_tokens: int | None = values["max_tokens"]

        self.imports()
        import torch
        from pocket_tts import TTSModel
        from pocket_tts.default_parameters import get_default_voice_for_language

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
            load_kwargs["temp"] = temperature
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

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The preset names of pocket-tts's built-in voice table, with the
        recording each was made from as its description. The table is private
        (`pocket_tts.utils.utils._ORIGINS_OF_PREDEFINED_VOICES`), so a version
        without it is reported as unsupported rather than guessed at."""
        try:
            from pocket_tts.utils import utils as pocket_utils
        except ImportError as exc:
            raise ConfigError(
                "The 'pocket' module requires the pocket extra: pip install tts-engine[pocket]"
            ) from exc
        presets = getattr(pocket_utils, "_ORIGINS_OF_PREDEFINED_VOICES", None)
        if not isinstance(presets, dict):
            raise VoiceListingUnsupported(
                "Pocket module: this pocket-tts version exposes no preset table; "
                "the preset voices are listed in the pocket-tts README "
                "(https://github.com/kyutai-labs/pocket-tts), and any .wav can be cloned"
            )
        return [
            VoiceInfo(
                id=name,
                name=name,
                config={"voice": name},
                description=str(source) if source else None,
            )
            for name, source in presets.items()
        ]

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
                if stop.is_set():
                    # Set while `next` was blocked: this frame was decoded
                    # after `stop` and is never fed.
                    break
                # The tensor to CPU/numpy here; the conversion is shared.
                callback(float_to_int16_pcm(chunk.detach().to("cpu").numpy()))
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
