"""Piper local-model module.

Runs Piper voices in process through `piper-tts`: onnxruntime for the VITS
voice model and espeak-ng, compiled into the package, for phonemes. Voices come
from `rhasspy/piper-voices` on the Hugging Face Hub (or local paths). `piper`
and `huggingface_hub` are imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require the `piper` extra — a
missing extra becomes a clear `ConfigError`.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from pathlib import Path

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    feed_with_lookahead,
    finite_number,
    optional_str,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)

log = logging.getLogger(__name__)

_VOICES_REPO = "rhasspy/piper-voices"
# Pinned so a change to the repository cannot change what a voice key loads.
_VOICES_REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
# LJ Speech: public domain (voice licenses vary, see specs/modules/piper.md).
_DEFAULT_VOICE = "en_US-ljspeech-medium"
_MIN_SPEED, _MAX_SPEED = 0.5, 2.0
# The Chinese phonemizer needs piper-tts's zh extra and a download directory.
_UNSUPPORTED_PHONEME_TYPES = {"pinyin"}
_LABEL = "Piper module"

# A voice file pair on the Hub (repo_id, onnx filename, revision), or a path.
_FileRef = tuple[str, str, str | None] | Path


def _catalog_voice(key: str) -> _FileRef:
    """`en_US-ljspeech-medium` → its .onnx in rhasspy/piper-voices, pinned."""
    parts = key.split("-")
    if len(parts) != 3 or not all(parts):
        raise ConfigError(
            "Piper module 'voice' must be a voice key <locale>-<name>-<quality>, "
            f"e.g. {_DEFAULT_VOICE!r}; got {key!r}"
        )
    locale, name, quality = parts
    language = locale.split("_")[0]
    return (
        _VOICES_REPO,
        f"{language}/{locale}/{name}/{quality}/{key}.onnx",
        _VOICES_REVISION,
    )


def _model_ref(config: dict, value: str) -> _FileRef:
    """Parse `hf://owner/repo/path/to/voice.onnx[@revision]`, else a path."""
    if not value.startswith("hf://"):
        return resolve_path(config, value)
    ref, revision = value[len("hf://") :], None
    if "@" in ref:
        ref, revision = ref.rsplit("@", 1)
    parts = ref.split("/")
    if len(parts) < 3 or not all(parts) or revision == "":
        raise ConfigError(
            "Piper module 'model' must be hf://owner/repo/path/to/voice.onnx"
            "[@revision] or a local path"
        )
    return "/".join(parts[:2]), "/".join(parts[2:]), revision


class PiperModule(TTSModule):
    kind = "local"

    def __init__(self, config: dict) -> None:
        voice_key = optional_str(config, "voice", label=_LABEL)
        model = optional_str(config, "model", label=_LABEL)
        if voice_key is not None and model is not None:
            raise ConfigError("Piper module accepts 'voice' or 'model', not both")
        if model is not None:
            ref = _model_ref(config, model)
        else:
            voice_key = voice_key or _DEFAULT_VOICE
            ref = _catalog_voice(voice_key)

        speaker = config.get("speaker")
        if speaker is not None and (
            isinstance(speaker, bool)
            or not isinstance(speaker, int | str)
            or (isinstance(speaker, int) and speaker < 0)
            or speaker == ""
        ):
            raise ConfigError(
                "Piper module 'speaker' must be a speaker name or an index >= 0"
            )

        speed = finite_number(
            config, "speed", label=_LABEL, minimum=_MIN_SPEED, maximum=_MAX_SPEED
        )

        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import (
                RemoteEntryNotFoundError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:
            raise ConfigError(
                "The 'piper' module requires the piper extra: "
                "pip install tts-engine[piper]"
            ) from exc

        if isinstance(ref, Path):
            # Checked here: a missing .onnx would surface as an opaque
            # onnxruntime error from PiperVoice.load.
            for path in (ref, Path(f"{ref}.json")):
                if not path.is_file():
                    raise ConfigError(f"Piper module 'model' file not found: {path}")
            onnx_path, json_path = str(ref), f"{ref}.json"
        else:
            repo_id, filename, revision = ref
            try:
                onnx_path = hf_hub_download(repo_id, filename, revision=revision)
                json_path = hf_hub_download(
                    repo_id, f"{filename}.json", revision=revision
                )
            except (
                RemoteEntryNotFoundError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            ) as exc:
                # The configured voice or reference is wrong. Not
                # EntryNotFoundError: LocalEntryNotFoundError is a cache miss.
                raise ConfigError(f"Piper module: {exc}") from exc
            except Exception as exc:
                raise TTSError(f"piper model load failed: {exc}") from exc

        try:
            voice = PiperVoice.load(onnx_path, config_path=json_path)
        except Exception as exc:
            raise TTSError(f"piper model load failed: {exc}") from exc

        voice_config = voice.config
        phoneme_type = getattr(voice_config.phoneme_type, "value", None)
        if phoneme_type in _UNSUPPORTED_PHONEME_TYPES:
            raise ConfigError(
                f"Piper module: {phoneme_type} voices (Chinese) are not supported"
            )
        try:
            # Fails for a phonemizer whose package piper-tts leaves to its own
            # extras (Japanese, Thai); also initializes espeak-ng now.
            voice.phonemize("a")
        except ImportError as exc:
            raise ConfigError(
                f"Piper module: this voice's {phoneme_type} phonemizer needs a "
                f"package the piper extra does not install: {exc}"
            ) from exc
        except Exception as exc:
            raise TTSError(f"piper model load failed: {exc}") from exc

        syn_kwargs: dict = {}
        if speaker is not None:
            syn_kwargs["speaker_id"] = self._speaker_id(voice_config, speaker)
        if speed is not None:
            syn_kwargs["length_scale"] = voice_config.length_scale / speed

        self._voice = voice
        self._syn_config = SynthesisConfig(**syn_kwargs)
        self._sample_rate = int(voice_config.sample_rate)
        self._slice_bytes = slice_bytes_for(self._sample_rate)
        log.info(
            "piper voice loaded: voice=%s rate=%d speakers=%d",
            voice_key or model,
            self._sample_rate,
            voice_config.num_speakers,
        )

    @staticmethod
    def _speaker_id(voice_config, speaker: int | str) -> int:
        """The index for `speaker` (a name or an index), checked against the
        voice: the library only fails on an unknown one at synthesis."""
        count = voice_config.num_speakers
        if count <= 1:
            raise ConfigError("Piper module 'speaker': this voice has one speaker")
        if isinstance(speaker, str):
            names = voice_config.speaker_id_map
            if speaker not in names:
                examples = ", ".join(list(names)[:3])
                raise ConfigError(
                    f"Piper module 'speaker' {speaker!r} is not in this voice "
                    f"({count} speakers, e.g. {examples})"
                )
            return names[speaker]
        if speaker >= count:
            raise ConfigError(
                f"Piper module 'speaker' must be below {count} for this voice"
            )
        return speaker

    def close(self) -> None:
        # Drop the voice so its ONNX session can be collected; the module is done.
        vars(self).pop("_voice", None)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _synthesize(self, segment: str) -> Iterator[bytes]:
        """Yield the int16 PCM of each sentence Piper makes of `segment`."""
        chunks = iter(self._voice.synthesize(segment, syn_config=self._syn_config))
        while True:
            try:
                chunk = next(chunks)
            except StopIteration:
                return
            except Exception as exc:
                raise TTSError(f"piper synthesis failed: {exc}") from exc
            if chunk.sample_rate != self._sample_rate:
                raise TTSError(
                    f"piper produced {chunk.sample_rate} Hz audio, "
                    f"expected {self._sample_rate} Hz"
                )
            yield chunk.audio_int16_bytes

    def _feed(
        self,
        segments: Iterable[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        # Each sentence arrives whole: synthesize the next one while this one
        # plays (no gap), and feed in slices (prompt cancellation).
        feed_with_lookahead(
            segments, self._synthesize, callback, stop, slice_bytes=self._slice_bytes
        )

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(stop: threading.Event) -> None:
            self._feed(segment_sentences([text]), callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # Piper needs a whole prompt per call: group the pieces into sentences
        # as they arrive and synthesize each, one ahead of playback.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._feed(segment_sentences(pieces), callback, stop)

        await run_text_stream_worker(text_stream, _worker)
