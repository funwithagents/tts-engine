"""OpenAI speech module.

Behind the `openai` extra: `openai` is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

`/v1/audio/speech` streams headerless s16le PCM (24 kHz on OpenAI) over a
chunked HTTP body, so the bytes are fed unchanged — re-aligned to whole samples
and cut into 100 ms slices so a cancel is honored promptly. With `base_url` and
`sample_rate` the same request reaches OpenAI-compatible servers. It takes one
complete input per request, so a text stream is grouped into sentences (see
specs/modules/openai.md).
"""

import contextlib
import logging
import math
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

log = logging.getLogger(__name__)

_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_MODEL = "gpt-4o-mini-tts"
_MIN_SPEED, _MAX_SPEED = 0.25, 4.0
# A WAV body (some compatible servers ignore `response_format`) starts with this.
_WAV_MAGIC = b"RIFF"


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"OpenAI module '{key}' must be a non-empty string")
    return value


class OpenAIModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigError(
                "The 'openai' module requires the openai extra: "
                "pip install tts-engine[openai]"
            ) from exc

        api_key = self._resolve_api_key(config)

        voice = config.get("voice")
        if not isinstance(voice, str) or not voice:
            raise ConfigError("OpenAI module requires a non-empty 'voice'")

        model = config.get("model", _DEFAULT_MODEL)
        if not isinstance(model, str) or not model:
            raise ConfigError("OpenAI module 'model' must be a non-empty string")

        instructions = _optional_str(config, "instructions")

        speed = config.get("speed")
        if speed is not None and (
            isinstance(speed, bool)
            or not isinstance(speed, int | float)
            or not math.isfinite(speed)
            or not _MIN_SPEED <= speed <= _MAX_SPEED
        ):
            raise ConfigError(
                f"OpenAI module 'speed' must be a number in "
                f"[{_MIN_SPEED}, {_MAX_SPEED}]"
            )

        base_url = _optional_str(config, "base_url")

        sample_rate = config.get("sample_rate", _DEFAULT_SAMPLE_RATE)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate <= 0
        ):
            raise ConfigError("OpenAI module 'sample_rate' must be a positive integer")
        if sample_rate != _DEFAULT_SAMPLE_RATE and base_url is None:
            # OpenAI's own `pcm` is fixed at 24 kHz; the field only declares the
            # rate of a compatible server's output.
            raise ConfigError(
                "OpenAI module 'sample_rate' other than 24000 requires 'base_url' "
                "(OpenAI's pcm output is fixed at 24 kHz)"
            )
        self._sample_rate = sample_rate
        # Feed at most 100 ms per callback: `AudioPlayer.feed` blocks for the
        # whole chunk, and `stop` is polled between slices.
        self._slice_bytes = 2 * max(1, sample_rate // 10)

        # Only the fields that were set: the server's own defaults apply to the rest.
        self._request_kwargs: dict = {
            "model": model,
            "voice": voice,
            "response_format": "pcm",
        }
        if instructions is not None:
            self._request_kwargs["instructions"] = instructions
        if speed is not None:
            self._request_kwargs["speed"] = float(speed)

        client_kwargs: dict = {"api_key": api_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env``."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("OpenAI module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("OpenAI module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"OpenAI module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "OpenAI module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one text and feed its PCM, polling `stop`. Runs on the
        worker thread."""
        if not text.strip():
            return  # the API rejects an empty input; nothing to say

        # Leaving the stack closes the HTTP response, on every path.
        with contextlib.ExitStack() as stack:
            try:
                response = stack.enter_context(
                    self._client.audio.speech.with_streaming_response.create(
                        input=text, **self._request_kwargs
                    )
                )
                chunks = response.iter_bytes()
            except Exception as exc:
                raise TTSError(f"OpenAI request failed: {exc}") from exc

            # Bytes not fed yet: an odd byte (a sample split across chunks), or
            # the first few bytes until the WAV check has seen four.
            pending = b""
            checked = False
            while not stop.is_set():
                # Advance inside the provider try so SDK/transport errors become
                # TTSError; feed outside it so a playback failure keeps its own
                # identity.
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"OpenAI request failed: {exc}") from exc
                data = pending + chunk
                if not checked:
                    if len(data) < len(_WAV_MAGIC):
                        pending = data
                        continue
                    if data.startswith(_WAV_MAGIC):
                        raise TTSError(
                            "OpenAI-compatible server returned WAV, not raw pcm"
                        )
                    checked = True
                whole = len(data) - len(data) % 2
                data, pending = data[:whole], data[whole:]
                for start in range(0, len(data), self._slice_bytes):
                    if stop.is_set():
                        return
                    callback(data[start : start + self._slice_bytes])

            # A body shorter than the WAV check: feed its whole samples.
            whole = len(pending) - len(pending) % 2
            if not checked and whole and not stop.is_set():
                callback(pending[:whole])

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
        # The speech endpoint takes one complete input per request: group the
        # pieces into sentences as they arrive and request each, in one worker.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            for sentence in segment_sentences(pieces):
                if stop.is_set():
                    return
                self._generate(sentence, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
