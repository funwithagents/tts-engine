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

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    feed_with_lookahead,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

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
# 100 ms per callback: `AudioPlayer.feed` blocks for the whole chunk, and
# `stop` is polled between slices.
_SLICE_BYTES = 2 * (_SAMPLE_RATE // 10)

# A model or voices file: (repo_id, filename, revision) on the Hub, or a path.
_FileRef = tuple[str, str, str | None] | Path


def _to_int16_pcm(audio) -> bytes:
    """Convert float32 samples (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Kokoro module '{key}' must be a non-empty string")
    return value


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
    def __init__(self, config: dict) -> None:
        voice = _optional_str(config, "voice") or _DEFAULT_VOICE
        language = _optional_str(config, "language")
        if language is None:
            language = _LANGUAGE_BY_PREFIX.get(voice[0])
            if language is None:
                raise ConfigError(
                    f"Kokoro module: no default language for voice {voice!r}; "
                    "set 'language' (an espeak-ng code such as 'en-us')"
                )

        speed = config.get("speed")
        if speed is not None and (
            isinstance(speed, bool)
            or not isinstance(speed, int | float)
            or not _MIN_SPEED <= speed <= _MAX_SPEED
        ):
            raise ConfigError(
                f"Kokoro module 'speed' must be a number in [{_MIN_SPEED}, {_MAX_SPEED}]"
            )

        model_ref = _file_ref(
            config, "model", _optional_str(config, "model") or _DEFAULT_MODEL
        )
        voices_ref = _file_ref(
            config, "voices", _optional_str(config, "voices") or _DEFAULT_VOICES
        )

        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import (
                RemoteEntryNotFoundError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise ConfigError(
                "The 'kokoro' module requires the kokoro extra: "
                "pip install tts-engine[kokoro]"
            ) from exc

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
            kokoro = Kokoro(model_path, voices_path)
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
            create_kwargs["speed"] = float(speed)

        self._kokoro = kokoro
        self._create_kwargs = create_kwargs
        log.info(
            "kokoro model loaded: model=%s voice=%s language=%s providers=%s",
            model_path,
            voice,
            language,
            kokoro.sess.get_providers(),
        )

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
        return _to_int16_pcm(audio)

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
