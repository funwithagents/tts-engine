"""Piper local-model module.

Runs Piper voices in process through `piper-tts`: onnxruntime for the VITS
voice model and espeak-ng, compiled into the package, for phonemes. Voices come
from `rhasspy/piper-voices` on the Hugging Face Hub (or local paths). `piper`
and `huggingface_hub` are imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require the `piper` extra — a
missing extra becomes a clear `ConfigError`.
"""

import json
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    feed_with_lookahead,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)
from tts_engine.modules.fields import Field

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
    title = "Piper"
    extra = "piper"
    fields = (
        Field(
            "voice",
            "string",
            "A voice of the rhasspy/piper-voices catalog, as "
            "<locale>-<name>-<quality>, such as 'en_GB-alba-medium'. Not "
            "together with 'model'.",
            implied=_DEFAULT_VOICE,
        ),
        Field(
            "model",
            "string",
            "A voice outside the catalog: hf://owner/repo/path/voice.onnx[@revision] "
            "or a local .onnx path, with its .onnx.json next to it. Not "
            "together with 'voice'.",
            advanced=True,
        ),
        Field(
            "speaker",
            ("string", "integer"),
            "For a voice with several speakers, the one to use: a speaker "
            "name, or an index from 0. Absent, the voice's default speaker.",
            minimum=0,
            message="Piper module 'speaker' must be a speaker name or an index >= 0",
        ),
        Field(
            "speed",
            "number",
            "The speaking rate, 1 being the voice's own pace.",
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
    )

    @classmethod
    def _ref(cls, values: dict[str, Any], config: dict) -> _FileRef:
        """Where the voice's .onnx is (its .onnx.json sits next to it)."""
        if values["model"] is not None:
            return _model_ref(config, values["model"])
        return _catalog_voice(values["voice"] or _DEFAULT_VOICE)

    @classmethod
    def check_config(cls, values: dict[str, Any], config: dict) -> None:
        if values["voice"] is not None and values["model"] is not None:
            raise ConfigError("Piper module accepts 'voice' or 'model', not both")
        cls._ref(values, config)

    @classmethod
    def _files(cls, config: dict) -> dict[str, _FileRef]:
        """The two files of the configured voice, by item key."""
        ref = cls._ref(cls.validated(config), config)
        if isinstance(ref, Path):
            return {"model": ref, "config": Path(f"{ref}.json")}
        repo_id, filename, revision = ref
        return {"model": ref, "config": (repo_id, f"{filename}.json", revision)}

    @classmethod
    def items(cls, config: dict) -> list[dict[str, Any]]:
        try:
            from huggingface_hub import try_to_load_from_cache
        except ImportError:
            try_to_load_from_cache = None
        found = []
        for key, ref in cls._files(config).items():
            item: dict[str, Any] = {
                "id": f"piper:{key}",
                "title": f"Piper voice {key} file",
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
                    "detail": "The piper extra is not installed.",
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
        files = cls._files(config)
        for item in cls.items(config):
            if item["status"] == "ready" or not item["preparable"]:
                continue
            ref = files[item["id"].split(":", 1)[1]]
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
                    "The 'piper' module requires the piper extra: "
                    "pip install tts-engine[piper]"
                ) from exc
            except Exception as exc:
                raise TTSError(f"piper download failed: {exc}") from exc
            if on_progress is not None:
                on_progress(
                    {"id": item["id"], "fraction": 1.0, "message": f"{filename} ready"}
                )

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        voice_key: str | None = values["voice"]
        model: str | None = values["model"]
        if model is None:
            voice_key = voice_key or _DEFAULT_VOICE
        ref = self._ref(values, config)
        speaker: int | str | None = values["speaker"]
        speed: float | None = values["speed"]
        threads: int | None = values["threads"]

        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import (
                RemoteEntryNotFoundError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
            from piper import PiperConfig, PiperVoice, SynthesisConfig
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
            if threads is None:
                voice = PiperVoice.load(onnx_path, config_path=json_path)
            else:
                # What PiperVoice.load does, on a session sized to `threads`
                # (its other options keep their defaults: CPU, no alignments).
                import onnxruntime as rt

                with open(json_path, encoding="utf-8") as f:
                    piper_config = PiperConfig.from_dict(json.load(f))
                options = rt.SessionOptions()
                options.intra_op_num_threads = threads
                voice = PiperVoice(
                    config=piper_config,
                    session=rt.InferenceSession(
                        onnx_path,
                        sess_options=options,
                        providers=["CPUExecutionProvider"],
                    ),
                )
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
            "piper voice loaded: voice=%s rate=%d speakers=%d threads=%s",
            voice_key or model,
            self._sample_rate,
            voice_config.num_speakers,
            threads or "default",
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

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The catalog's `voices.json` at the pinned commit: one entry per
        voice, or per speaker of a multi-speaker voice (`speaker` added to its
        config). The catalog has no gender field."""
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise ConfigError(
                "The 'piper' module requires the piper extra: pip install tts-engine[piper]"
            ) from exc
        try:
            path = hf_hub_download(
                _VOICES_REPO, "voices.json", revision=_VOICES_REVISION
            )
            with open(path, encoding="utf-8") as file:
                catalog = json.load(file)
        except Exception as exc:
            raise TTSError(f"{_LABEL}: voice listing failed: {exc}") from exc

        voices: list[VoiceInfo] = []
        for key, entry in catalog.items():
            language = entry.get("language") or {}
            code = language.get("code")
            place = ", ".join(
                part
                for part in (
                    language.get("name_english"),
                    language.get("country_english"),
                )
                if part
            )
            common = {
                "languages": (code,) if code else (),
                "description": place or None,
                "tags": tuple(tag for tag in (entry.get("quality"),) if tag),
            }
            speakers = entry.get("speaker_id_map") or {}
            if entry.get("num_speakers", 1) <= 1 or not speakers:
                voices.append(
                    VoiceInfo(id=key, name=key, config={"voice": key}, **common)
                )
                continue
            for speaker in sorted(speakers, key=speakers.__getitem__):
                voices.append(
                    VoiceInfo(
                        id=f"{key}/{speaker}",
                        name=f"{key} / {speaker}",
                        config={"voice": key, "speaker": speaker},
                        **common,
                    )
                )
        return voices

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
