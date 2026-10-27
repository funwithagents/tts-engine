"""Mistral Voxtral TTS module.

Behind the `mistral` extra: `mistralai` is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

The speech endpoint streams headerless float32 PCM at 24 kHz over server-sent
events; the module converts it to int16 and feeds it in 100 ms slices, so a
cancel is honored promptly even though the API sends up to 2 s per event. It
takes one complete input per request, so a text stream is grouped into
sentences (see specs/modules/mistral.md).
"""

import base64
import logging
import math
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    cleanup,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
)

log = logging.getLogger(__name__)

_SAMPLE_RATE = 24000
_DEFAULT_MODEL = "voxtral-mini-tts-3-0"
_DEFAULT_TIMEOUT = 30.0
# Feed at most 100 ms per callback: `AudioPlayer.feed` blocks for the whole
# chunk, and `stop` is polled between slices.
_SLICE_BYTES = 2 * (_SAMPLE_RATE // 10)
_FLOAT_BYTES = 4


def _to_int16_pcm(data: bytes) -> bytes:
    """Convert float32 little-endian samples (values ~[-1, 1]) to signed-16-bit
    little-endian PCM bytes — the audio-format contract's encoding half."""
    arr = np.frombuffer(data, dtype="<f4").copy()
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Mistral module '{key}' must be a non-empty string")
    return value


class MistralModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ConfigError(
                "The 'mistral' module requires the mistral extra: "
                "pip install tts-engine[mistral]"
            ) from exc

        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("Mistral module requires a non-empty 'voice_id'")

        model = config.get("model", _DEFAULT_MODEL)
        if not isinstance(model, str) or not model:
            raise ConfigError("Mistral module 'model' must be a non-empty string")

        expressiveness = config.get("expressiveness")
        if expressiveness is not None and (
            isinstance(expressiveness, bool)
            or not isinstance(expressiveness, int | float)
            or not math.isfinite(expressiveness)
            or expressiveness < 1.0
        ):
            raise ConfigError(
                "Mistral module 'expressiveness' must be a finite number >= 1.0"
            )

        server_url = _optional_str(config, "server_url")

        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ConfigError("Mistral module 'timeout' must be a finite number > 0")

        # Only the fields that were set: the API's own defaults apply to the
        # rest. `timeout_ms` bounds the wait for the response and for each
        # read of the stream, so a stalled request fails instead of holding
        # the worker (and a cancel) indefinitely.
        self._request_kwargs: dict = {
            "model": model,
            "voice_id": voice_id,
            "response_format": "pcm",
            "stream": True,
            "timeout_ms": math.ceil(timeout * 1000),
        }
        if expressiveness is not None:
            self._request_kwargs["expressiveness"] = float(expressiveness)

        client_kwargs: dict = {"api_key": api_key}
        if server_url is not None:
            client_kwargs["server_url"] = server_url
        self._client = Mistral(**client_kwargs)

    @property
    def sample_rate(self) -> int:
        # The `pcm` response format is fixed at 24 kHz; the API has no rate option.
        return _SAMPLE_RATE

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env``."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("Mistral module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("Mistral module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"Mistral module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "Mistral module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one text and feed its PCM, polling `stop`. Runs on the
        worker thread."""
        try:
            events = self._client.audio.speech.complete(
                input=text, **self._request_kwargs
            )
        except Exception as exc:
            raise TTSError(f"Mistral request failed: {exc}") from exc

        carry = b""  # a float32 sample split across two deltas
        # Leaving the block closes the HTTP response, on every path; a failing
        # close never hides the utterance's own error.
        with cleanup(lambda: events.__exit__(None, None, None), "Mistral response"):
            while not stop.is_set():
                # Advance and decode inside the provider try so SDK/transport
                # errors become TTSError; feed outside it so a playback failure
                # keeps its own identity.
                try:
                    event = next(events)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"Mistral request failed: {exc}") from exc
                if event.event == "speech.audio.done":
                    break
                if event.event != "speech.audio.delta":
                    continue
                try:
                    data = carry + base64.b64decode(event.data.audio_data)
                except Exception as exc:
                    raise TTSError(f"Mistral request failed: {exc}") from exc
                whole = len(data) - len(data) % _FLOAT_BYTES
                data, carry = data[:whole], data[whole:]
                pcm = _to_int16_pcm(data)
                for start in range(0, len(pcm), _SLICE_BYTES):
                    if stop.is_set():
                        return
                    callback(pcm[start : start + _SLICE_BYTES])

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
