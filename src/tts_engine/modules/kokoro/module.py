"""Kokoro local-model module.

Runs Kokoro-82M in process through `kokoro-onnx`: onnxruntime for the model
and a bundled espeak-ng for phonemes, no torch. The model and voices files come
from the Hugging Face Hub (or local paths). `kokoro_onnx` and `huggingface_hub`
are imported lazily inside `__init__` so `import tts_engine` and `load_module`
never require the `kokoro` extra — a missing extra becomes a clear `ConfigError`.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    feed_with_lookahead,
    float_to_int16_pcm,
    normalize_gender,
    optional_str,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

# kokoro-onnx's own release files, mirrored on the Hub; pinned so a change to
# the mirror cannot change what loads (see specs/modules/kokoro.md).
_FILES_REVISION = "8d07950c9b6c87ce6809e9bba7bd494336217c2a"
_DEFAULT_MODEL = f"hf://fastrtc/kokoro-onnx/kokoro-v1.0.onnx@{_FILES_REVISION}"
_DEFAULT_VOICES = f"hf://fastrtc/kokoro-onnx/voices-v1.0.bin@{_FILES_REVISION}"
_DEFAULT_VOICE = "af_heart"
# A voice name's first letter is its language: the espeak-ng code to use.
_LANGUAGE_BY_PREFIX = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "cmn",
}
_MIN_SPEED, _MAX_SPEED = 0.5, 2.0
_SAMPLE_RATE = 24000
_SLICE_BYTES = slice_bytes_for(_SAMPLE_RATE)
_LABEL = "Kokoro module"

# A model or voices file: (repo_id, filename, revision) on the Hub, or a path.
_FileRef = tuple[str, str, str | None] | Path


def _file_ref(config: dict, key: str, value: str) -> _FileRef:
    """Parse `hf://owner/repo/path/to/file[@revision]`, else resolve a path."""
    if not value.startswith("hf://"):
        return resolve_path(config, value)
    ref, revision = value[len("hf://") :], None
    if "@" in ref:
        ref, revision = ref.rsplit("@", 1)
    parts = ref.split("/")
    if len(parts) < 3 or not all(parts) or revision == "":
        raise ConfigError(
            f"Kokoro module '{key}' must be hf://owner/repo/path/to/file[@revision] "
            "or a local path"
        )
    return "/".join(parts[:2]), "/".join(parts[2:]), revision


class KokoroModule(TTSModule):
    kind = "local"
    title = "Kokoro"
    extra = "kokoro"
    fields = (
        Field(
            "voice",
            "string",
            "The voice's name in the voices file. Its first letter is its "
            "language, its second its gender.",
            implied=_DEFAULT_VOICE,
        ),
        Field(
            "language",
            "string",
            "The espeak-ng language code to pronounce with, such as 'en-us'. "
            "Absent, it follows the voice's first letter.",
        ),
        Field(
            "speed",
            "number",
            "The speaking rate, 1 being the voice's own.",
            implied=1.0,
            minimum=_MIN_SPEED,
            maximum=_MAX_SPEED,
        ),
        Field(
            "threads",
            "integer",
            "The number of CPU threads the model runs on. Absent, the runtime decides.",
            minimum=1,
            advanced=True,
        ),
        Field(
            "model",
            "string",
            "The model file: hf://owner/repo/path[@revision] or a local path.",
            implied=_DEFAULT_MODEL,
            advanced=True,
        ),
        Field(
            "voices",
            "string",
            "The voices file: hf://owner/repo/path[@revision] or a local path.",
            implied=_DEFAULT_VOICES,
            advanced=True,
        ),
    )

    @classmethod
    def _refs(cls, values: dict[str, Any], config: dict) -> dict[str, _FileRef]:
        """Where the model and the voices files are, by field name."""
        return {
            "model": _file_ref(config, "model", values["model"] or _DEFAULT_MODEL),
            "voices": _file_ref(config, "voices", values["voices"] or _DEFAULT_VOICES),
        }

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        voice = values["voice"] or _DEFAULT_VOICE
        if values["language"] is None and voice[0] not in _LANGUAGE_BY_PREFIX:
            raise ConfigError(
                f"Kokoro module: no default language for voice {voice!r}; "
                "set 'language' (an espeak-ng code such as 'en-us')"
            )
        cls._refs(values, config)

    @classmethod
    def check_files(cls, config: dict) -> list[dict[str, Any]]:
        refs = cls._refs(cls.validated(config), config)
        try:
            from huggingface_hub import try_to_load_from_cache
        except ImportError:
            try_to_load_from_cache = None
        found = []
        for key, ref in refs.items():
            item: dict[str, Any] = {
                "id": f"kokoro:{key}",
                "title": f"Kokoro {key} file",
            }
            if isinstance(ref, Path):
                present = ref.is_file()
                item |= {
                    "status": "ready" if present else "missing",
                    "detail": str(ref) if present else f"File not found: {ref}",
                    "preparable": False,
                }
            elif try_to_load_from_cache is None:
                item |= {
                    "status": "unknown",
                    "detail": "The kokoro extra is not installed.",
                    "preparable": True,
                }
            else:
                repo_id, filename, revision = ref
                cached = try_to_load_from_cache(repo_id, filename, revision=revision)
                present = isinstance(cached, str)
                item |= {
                    "status": "ready" if present else "missing",
                    "detail": (
                        cached
                        if present
                        else f"{filename} of {repo_id} is not downloaded yet."
                    ),
                    "preparable": True,
                }
            found.append(item)
        return found

    @classmethod
    def prepare(
        cls,
        config: dict,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        for item in cls.check_files(config):
            if item["status"] == "ready" or not item["preparable"]:
                continue
            key = item["id"].split(":", 1)[1]
            ref = cls._refs(cls.validated(config), config)[key]
            assert not isinstance(ref, Path)
            repo_id, filename, revision = ref
            if on_progress is not None:
                on_progress(
                    {
                        "id": item["id"],
                        "fraction": None,
                        "message": f"Downloading {filename}",
                    }
                )
            try:
                from huggingface_hub import hf_hub_download

                hf_hub_download(repo_id, filename, revision=revision)
            except ImportError as exc:
                raise ConfigError(
                    "The 'kokoro' module requires the kokoro extra: "
                    "pip install tts-engine[kokoro]"
                ) from exc
            except Exception as exc:
                raise TTSError(f"kokoro download failed: {exc}") from exc
            if on_progress is not None:
                on_progress(
                    {"id": item["id"], "fraction": 1.0, "message": f"{filename} ready"}
                )

    @classmethod
    def imports(cls) -> None:
        try:
            from huggingface_hub import hf_hub_download  # noqa: F401
            from huggingface_hub.errors import (  # noqa: F401
                RemoteEntryNotFoundError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
            from kokoro_onnx import Kokoro  # noqa: F401
        except ImportError as exc:
            raise ConfigError(
                "The 'kokoro' module requires the kokoro extra: "
                "pip install tts-engine[kokoro]"
            ) from exc

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        voice: str = values["voice"] or _DEFAULT_VOICE
        language: str = values["language"] or _LANGUAGE_BY_PREFIX[voice[0]]
        speed: float | None = values["speed"]
        threads: int | None = values["threads"]
        refs = self._refs(values, config)
        model_ref, voices_ref = refs["model"], refs["voices"]

        self.imports()
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import (
            RemoteEntryNotFoundError,
            RepositoryNotFoundError,
            RevisionNotFoundError,
        )
        from kokoro_onnx import Kokoro

        # Checked here: kokoro-onnx opens the model before its own file check,
        # so a missing model would surface as an opaque onnxruntime error.
        for key, ref in (("model", model_ref), ("voices", voices_ref)):
            if isinstance(ref, Path) and not ref.is_file():
                raise ConfigError(f"Kokoro module '{key}' file not found: {ref}")

        def fetch(ref: _FileRef) -> str:
            if isinstance(ref, Path):
                return str(ref)
            repo_id, filename, revision = ref
            return hf_hub_download(repo_id, filename, revision=revision)

        try:
            model_path, voices_path = fetch(model_ref), fetch(voices_ref)
        except (
            RemoteEntryNotFoundError,
            RepositoryNotFoundError,
            RevisionNotFoundError,
        ) as exc:
            # The configured reference is wrong. Not EntryNotFoundError: its
            # LocalEntryNotFoundError sibling means offline and not cached.
            raise ConfigError(f"Kokoro module: {exc}") from exc
        except Exception as exc:
            raise TTSError(f"kokoro model load failed: {exc}") from exc

        try:
            if threads is None:
                kokoro = Kokoro(model_path, voices_path)
            else:
                # The session the library would build, with the thread count:
                # its provider rule, then its constructor for a given session.
                import onnxruntime as rt
                from kokoro_onnx.session import resolve_providers

                options = rt.SessionOptions()
                options.intra_op_num_threads = threads
                session = rt.InferenceSession(
                    model_path, sess_options=options, providers=resolve_providers()
                )
                kokoro = Kokoro.from_session(session, voices_path)
        except Exception as exc:
            raise TTSError(f"kokoro model load failed: {exc}") from exc

        voices = kokoro.get_voices()
        if voice not in voices:
            raise ConfigError(
                f"Kokoro module 'voice' {voice!r} is not in the voices file "
                f"({len(voices)} voices, e.g. {', '.join(voices[:3])})"
            )
        try:
            # The library only rejects an unknown language at the first
            # synthesis; this also pays espeak-ng's one-time setup now.
            kokoro.tokenizer.phonemize("a", language)
        except Exception as exc:
            raise ConfigError(f"Kokoro module 'language' {language!r}: {exc}") from exc

        create_kwargs: dict = {"voice": voice, "lang": language}
        if speed is not None:
            create_kwargs["speed"] = speed

        self._kokoro = kokoro
        self._create_kwargs = create_kwargs
        log.info(
            "kokoro model loaded: model=%s voice=%s language=%s providers=%s threads=%s",
            model_path,
            voice,
            language,
            kokoro.sess.get_providers(),
            threads or "default",
        )

    def close(self) -> None:
        # Drop the runtime so its ONNX session can be collected; the module is done.
        vars(self).pop("_kokoro", None)

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The voice names in the voices file (the configured `voices`, else
        the pinned default), read without loading the model. A name's first
        letter is its language (the default-language table), its second its
        gender (`f` / `m`)."""
        voices_ref = _file_ref(
            config,
            "voices",
            optional_str(config, "voices", label=_LABEL) or _DEFAULT_VOICES,
        )
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise ConfigError(
                "The 'kokoro' module requires the kokoro extra: pip install tts-engine[kokoro]"
            ) from exc
        if isinstance(voices_ref, Path):
            if not voices_ref.is_file():
                raise ConfigError(
                    f"Kokoro module 'voices' file not found: {voices_ref}"
                )
            path = str(voices_ref)
        else:
            repo_id, filename, revision = voices_ref
            try:
                path = hf_hub_download(repo_id, filename, revision=revision)
            except Exception as exc:
                raise TTSError(f"{_LABEL}: voice listing failed: {exc}") from exc
        try:
            with np.load(path) as archive:
                names = sorted(archive.files)
        except Exception as exc:
            raise TTSError(
                f"{_LABEL}: cannot read the voices file {path}: {exc}"
            ) from exc

        voices: list[VoiceInfo] = []
        for name in names:
            language = _LANGUAGE_BY_PREFIX.get(name[:1])
            voices.append(
                VoiceInfo(
                    id=name,
                    name=name,
                    config={"voice": name},
                    languages=(language,) if language else (),
                    gender=normalize_gender(name[1:2]),
                )
            )
        return voices

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    def _create(self, sentence: str) -> bytes:
        """Synthesize one sentence to int16 PCM (blocking, the whole sentence)."""
        try:
            audio, rate = self._kokoro.create(sentence, **self._create_kwargs)
        except Exception as exc:
            raise TTSError(f"kokoro synthesis failed: {exc}") from exc
        if rate != _SAMPLE_RATE:
            raise TTSError(
                f"kokoro produced {rate} Hz audio, expected {_SAMPLE_RATE} Hz"
            )
        return float_to_int16_pcm(audio)

    def _feed(
        self,
        sentences: Iterable[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        # `create` returns a whole sentence: synthesize the next one while
        # this one plays (no gap), and feed in slices (prompt cancellation).
        feed_with_lookahead(
            sentences,
            lambda sentence: [self._create(sentence)],
            callback,
            stop,
            slice_bytes=_SLICE_BYTES,
        )

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # A sentence per call even for a complete text: `create` returns
        # nothing until its whole input is synthesized.
        def _worker(stop: threading.Event) -> None:
            self._feed(segment_sentences([text]), callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # kokoro-onnx needs a whole prompt per call: group the pieces into
        # sentences as they arrive and synthesize each, one ahead of playback.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._feed(segment_sentences(pieces), callback, stop)

        await run_text_stream_worker(text_stream, _worker)
