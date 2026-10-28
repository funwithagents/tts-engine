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
import threading
from collections.abc import AsyncIterator, Callable, Iterator

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    TTSOptions,
    cleanup,
    finite_number,
    float_to_int16_pcm,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)

log = logging.getLogger(__name__)

_SAMPLE_RATE = 24000
_DEFAULT_MODEL = "voxtral-mini-tts-3-0"
_DEFAULT_TIMEOUT = 30.0
_LABEL = "Mistral module"
_SLICE_BYTES = slice_bytes_for(_SAMPLE_RATE)
_FLOAT_BYTES = 4


def _to_int16_pcm(data: bytes) -> bytes:
    """Convert float32 little-endian sample bytes to the contract's int16."""
    return float_to_int16_pcm(np.frombuffer(data, dtype="<f4"))


class MistralModule(TTSModule):
    kind = "hosted"

    def __init__(self, config: dict) -> None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ConfigError(
                "The 'mistral' module requires the mistral extra: "
                "pip install tts-engine[mistral]"
            ) from exc

        api_key = resolve_api_key(config, label=_LABEL)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("Mistral module requires a non-empty 'voice_id'")

        model = optional_str(config, "model", label=_LABEL, default=_DEFAULT_MODEL)

        expressiveness = finite_number(
            config, "expressiveness", label=_LABEL, minimum=1.0
        )
        server_url = optional_str(config, "server_url", label=_LABEL)

        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )

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
            self._request_kwargs["expressiveness"] = expressiveness

        client_kwargs: dict = {"api_key": api_key}
        if server_url is not None:
            client_kwargs["server_url"] = server_url
        self._client = Mistral(**client_kwargs)

    def close(self) -> None:
        # The speakeasy SDK closes its HTTP client on context exit; there is
        # no separate close method. The module is done.
        self._client.__exit__(None, None, None)

    @property
    def sample_rate(self) -> int:
        # The `pcm` response format is fixed at 24 kHz; the API has no rate option.
        return _SAMPLE_RATE

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

        # Whole float32 samples (one split across two deltas waits for the
        # next), converted to int16 and fed in 100 ms slices.
        feeder = PCMFeeder(
            callback,
            stop,
            slice_bytes=_SLICE_BYTES,
            sample_bytes=_FLOAT_BYTES,
            convert=_to_int16_pcm,
        )
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
                    data = base64.b64decode(event.data.audio_data)
                except Exception as exc:
                    raise TTSError(f"Mistral request failed: {exc}") from exc
                if not feeder.feed(data):
                    return

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
