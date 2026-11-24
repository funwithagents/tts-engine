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
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    cleanup,
    finite_number,
    float_to_int16_pcm,
    key_env_field,
    normalize_gender,
    optional_str,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)
from tts_engine.modules.fields import Field

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
    title = "Mistral"
    extra = "mistral"
    fields = (
        Field(
            "voice_id",
            "string",
            "The voice to speak with: a preset voice's id or slug, or the id of "
            "a custom voice of the account.",
            required=True,
            message="Mistral module requires a non-empty 'voice_id'",
        ),
        Field(
            "model",
            "string",
            "The Mistral text-to-speech model.",
            default=_DEFAULT_MODEL,
        ),
        key_env_field("Mistral", "MISTRAL_API_KEY"),
        Field(
            "expressiveness",
            "number",
            "How strongly the speech follows the voice's character, 1 or more. "
            "Left unset, the service's own default applies.",
            minimum=1.0,
        ),
        Field(
            "server_url",
            "string",
            "The API base URL, for a regional or self-hosted endpoint.",
            advanced=True,
        ),
        Field(
            "timeout",
            "number",
            "Seconds to wait for the service before a stalled request fails.",
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
            advanced=True,
        ),
    )

    @classmethod
    def imports(cls) -> None:
        try:
            from mistralai.client import Mistral  # noqa: F401
        except ImportError as exc:
            raise ConfigError(
                "The 'mistral' module requires the mistral extra: "
                "pip install tts-engine[mistral]"
            ) from exc

    def __init__(
        self, config: dict, *, secrets: Mapping[str, str] | None = None
    ) -> None:
        values = self.validated(config)
        self.imports()
        from mistralai.client import Mistral

        api_key = self.resolve_api_key(config, secrets)

        voice_id: str = values["voice_id"]
        model: str = values["model"]
        expressiveness: float | None = values["expressiveness"]
        server_url: str | None = values["server_url"]
        timeout: float = values["timeout"]

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

    @classmethod
    def list_voices(
        cls, config: dict, *, secrets: Mapping[str, str] | None = None
    ) -> list[VoiceInfo]:
        """The presets and the account's custom voices, from
        `audio.voices.search()` followed page by page. Gender is given; age is
        in years when set."""
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ConfigError(
                "The 'mistral' module requires the mistral extra: "
                "pip install tts-engine[mistral]"
            ) from exc
        api_key = cls.resolve_api_key(config, secrets)
        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )
        client_kwargs: dict = {"api_key": api_key}
        server_url = optional_str(config, "server_url", label=_LABEL)
        if server_url is not None:
            client_kwargs["server_url"] = server_url
        client = Mistral(**client_kwargs)

        entries: list[Any] = []
        try:
            token: str | None = None
            while True:
                request: dict = {
                    "page_size": 100,
                    "timeout_ms": math.ceil(timeout * 1000),
                }
                if token is not None:
                    request["page_token"] = token
                page = client.audio.voices.search(**request)
                if page is None:
                    break
                entries.extend(page.result.data)
                next_token = page.result.next_page_token
                if (
                    not isinstance(next_token, str)
                    or not next_token
                    or next_token == token
                ):
                    break
                token = next_token
        except Exception as exc:
            raise TTSError(f"{_LABEL}: voice listing failed: {exc}") from exc
        finally:
            client.__exit__(None, None, None)

        return [
            VoiceInfo(
                id=voice.id,
                name=voice.name or voice.id,
                config={"voice_id": voice.id},
                languages=tuple(getattr(voice, "languages", None) or ()),
                gender=normalize_gender(getattr(voice, "gender", None)),
                age=None if getattr(voice, "age", None) is None else str(voice.age),
                description=getattr(voice, "description", None) or None,
                tags=tuple(getattr(voice, "tags", None) or ()),
            )
            for voice in entries
        ]

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
