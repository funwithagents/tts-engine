"""AudioFile fixture module: plays pre-recorded WAV files matched by text.

Not a TTS backend. Maps known sentences to 16-bit mono WAV files (with an
optional default file) so demos and test suites hear real audio with no API
key, extra, or network. Depends on nothing beyond the standard library.
"""

import threading
import wave
from collections.abc import Callable
from pathlib import Path

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    resolve_path,
    run_cancellable_worker,
)

_CHUNK_FRAMES = 4096


def _normalize(text: str) -> str:
    """Collapse whitespace runs to one space and strip both ends."""
    return " ".join(text.split())


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


class AudioFileModule(TTSModule):
    def __init__(self, config: dict) -> None:
        files = config.get("files", [])
        if not isinstance(files, list):
            raise ConfigError("AudioFile module 'files' must be a list")
        for i, entry in enumerate(files):
            if (
                not isinstance(entry, dict)
                or not _non_empty_str(entry.get("text"))
                or not _non_empty_str(entry.get("file"))
            ):
                raise ConfigError(
                    f"AudioFile module 'files[{i}]' must be an object with "
                    "non-empty 'text' and 'file'"
                )

        default_file = config.get("default_file")
        if "default_file" in config and not _non_empty_str(default_file):
            raise ConfigError(
                "AudioFile module 'default_file' must be a non-empty string"
            )

        if not files and default_file is None:
            raise ConfigError(
                "AudioFile module needs at least one 'files' entry or a 'default_file'"
            )

        self._entries: dict[str, Path] = {}
        for entry in files:
            key = _normalize(entry["text"])
            if key in self._entries:
                raise ConfigError(f"AudioFile module has a duplicate text: {key!r}")
            self._entries[key] = resolve_path(config, entry["file"])
        self._default: Path | None = (
            resolve_path(config, default_file) if default_file is not None else None
        )

        paths = list(self._entries.values())
        if self._default is not None:
            paths.append(self._default)
        self._sample_rate = self._check_headers(dict.fromkeys(paths))

    @staticmethod
    def _check_headers(paths: dict[Path, None]) -> int:
        """Read each WAV header; require 16-bit mono and one shared frame rate."""
        rate: int | None = None
        rate_path: Path | None = None
        for path in paths:
            try:
                with wave.open(str(path), "rb") as wav:
                    sampwidth = wav.getsampwidth()
                    nchannels = wav.getnchannels()
                    framerate = wav.getframerate()
            except (OSError, wave.Error) as exc:
                raise ConfigError(
                    f"AudioFile module cannot read {path}: {exc}"
                ) from exc
            if sampwidth != 2:
                raise ConfigError(
                    f"AudioFile module {path} must be 16-bit "
                    f"(got {sampwidth * 8}-bit samples)"
                )
            if nchannels != 1:
                raise ConfigError(
                    f"AudioFile module {path} must be mono (got {nchannels} channels)"
                )
            if rate is None:
                rate, rate_path = framerate, path
            elif framerate != rate:
                raise ConfigError(
                    f"AudioFile module files must share one frame rate: "
                    f"{rate_path} is {rate} Hz but {path} is {framerate} Hz"
                )
        assert rate is not None  # the constructor guarantees at least one path
        return rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        path = self._entries.get(_normalize(text), self._default)
        if path is None:
            raise TTSError(f"no audio file configured for text: {text!r}")

        def _worker(stop: threading.Event) -> None:
            try:
                wav = wave.open(str(path), "rb")  # noqa: SIM115 (closed by the with below)
            except (OSError, wave.Error) as exc:
                raise TTSError(f"AudioFile module cannot read {path}: {exc}") from exc
            with wav:
                while not stop.is_set():
                    try:
                        chunk = wav.readframes(_CHUNK_FRAMES)
                    except (OSError, wave.Error) as exc:
                        raise TTSError(
                            f"AudioFile module cannot read {path}: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    # Outside the try: callback errors keep their identity.
                    callback(chunk)

        await run_cancellable_worker(_worker)
