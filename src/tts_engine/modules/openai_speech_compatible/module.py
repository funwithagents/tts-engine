"""OpenAI speech-compatible module.

Behind the `openai-compatible` extra: the `openai` SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

`/v1/audio/speech` streams headerless s16le PCM (24 kHz on OpenAI) over a
chunked HTTP body, so the bytes are fed unchanged — re-aligned to whole samples
and cut into 100 ms slices so a cancel is honored promptly. With `base_url` and
`sample_rate` the same request reaches any OpenAI-compatible server, which is
what the endpoint is for once OpenAI's own models leave it on 2027-01-06. It takes one
complete input per request, so a text stream is grouped into sentences (see
specs/modules/openai-speech-compatible.md).
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    VoiceListingUnsupported,
    cleanup,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_MODEL = "gpt-4o-mini-tts-2025-03-20"
_DEFAULT_TIMEOUT = 30.0
_LABEL = "OpenAI speech-compatible module"
_MIN_SPEED, _MAX_SPEED = 0.25, 4.0
# A WAV body (some compatible servers ignore `response_format`) starts with this.
_WAV_MAGIC = b"RIFF"


class OpenAISpeechCompatibleModule(TTSModule):
    kind = "hosted"
    title = "OpenAI speech-compatible"
    extra = "openai-compatible"
    fields = (
        Field(
            "voice",
            "string",
            "The voice to speak with: one of OpenAI's built-in voices (marin, "
            "cedar, alloy, ...) or a voice name of the compatible server.",
            required=True,
            message="OpenAI speech-compatible module requires a non-empty 'voice'",
        ),
        Field(
            "model",
            "string",
            "The speech model, OpenAI's or the compatible server's.",
            default=_DEFAULT_MODEL,
        ),
        Field(
            "api_key_env",
            "string",
            "The environment variable holding the API key.",
            allow_empty=True,
            secret="env",
            message="OpenAI speech-compatible module 'api_key_env' must be a string",
        ),
        Field(
            "api_key",
            "string",
            "The API key itself. Prefer 'api_key_env', which keeps the key out of "
            "the config file; a server that needs no key takes any placeholder.",
            allow_empty=True,
            secret="literal",
            message="OpenAI speech-compatible module 'api_key' must be a string",
        ),
        Field(
            "instructions",
            "string",
            "A direction for the voice, such as its tone, pace or accent. Some "
            "models ignore it.",
        ),
        Field(
            "speed",
            "number",
            "The speaking rate, as a multiplier from 0.25 to 4.",
            implied=1.0,
            minimum=_MIN_SPEED,
            maximum=_MAX_SPEED,
        ),
        Field(
            "base_url",
            "string",
            "The base URL of an OpenAI-compatible server, including its /v1 "
            "path. Left empty, requests go to OpenAI.",
        ),
        Field(
            "timeout",
            "number",
            "Seconds to wait for the provider before a stalled request fails.",
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
            advanced=True,
        ),
        Field(
            "sample_rate",
            "integer",
            "The sample rate of the server's PCM output, in Hz. OpenAI's is "
            "always 24000; set it only with 'base_url', to match the server.",
            default=_DEFAULT_SAMPLE_RATE,
            exclusive_minimum=0,
            advanced=True,
            message=(
                "OpenAI speech-compatible module 'sample_rate' must be a positive "
                "integer"
            ),
        ),
    )

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        if values["sample_rate"] != _DEFAULT_SAMPLE_RATE and values["base_url"] is None:
            # OpenAI's own `pcm` is fixed at 24 kHz; the field only declares the
            # rate of a compatible server's output.
            raise ConfigError(
                "OpenAI speech-compatible module 'sample_rate' other than 24000 requires 'base_url' "
                "(OpenAI's pcm output is fixed at 24 kHz)"
            )

    @classmethod
    def imports(cls) -> None:
        try:
            from openai import OpenAI  # noqa: F401
        except ImportError as exc:
            raise ConfigError(
                "The 'openai-speech-compatible' module requires the openai-compatible extra: "
                "pip install tts-engine[openai-compatible]"
            ) from exc

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        self.imports()
        from openai import OpenAI

        api_key = resolve_api_key(config, label=_LABEL)

        voice: str = values["voice"]
        model: str = values["model"]
        instructions: str | None = values["instructions"]
        speed: float | None = values["speed"]
        base_url: str | None = values["base_url"]
        timeout: float = values["timeout"]
        sample_rate: int = values["sample_rate"]
        self._sample_rate = sample_rate
        self._slice_bytes = slice_bytes_for(sample_rate)

        # Only the fields that were set: the server's own defaults apply to the
        # rest. `timeout` bounds the wait for the response and for each read
        # of the body, so a stalled request fails instead of holding the
        # worker (and a cancel) for the SDK's 600 s default.
        self._request_kwargs: dict = {
            "model": model,
            "voice": voice,
            "response_format": "pcm",
            "timeout": timeout,
        }
        if instructions is not None:
            self._request_kwargs["instructions"] = instructions
        if speed is not None:
            self._request_kwargs["speed"] = speed

        client_kwargs: dict = {"api_key": api_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    def close(self) -> None:
        # Closes the SDK's pooled HTTP connections; the module is done.
        self._client.close()

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """`/v1/audio/speech` has no voice-listing endpoint; a server other
        than OpenAI's documents its own voices."""
        base_url = optional_str(config, "base_url", label=_LABEL)
        where = (
            f"the server at {base_url} documents its own voices"
            if base_url is not None
            else "OpenAI's built-in voices (marin, cedar, alloy, ...) are listed at "
            "https://platform.openai.com/docs/guides/text-to-speech"
        )
        raise VoiceListingUnsupported(
            f"OpenAI speech-compatible module: the speech API has no voice-listing endpoint; {where}"
        )

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one text and feed its PCM, polling `stop`. Runs on the
        worker thread."""
        if not text.strip():
            return  # the API rejects an empty input; nothing to say

        try:
            response_cm = self._client.audio.speech.with_streaming_response.create(
                input=text, **self._request_kwargs
            )
            response = response_cm.__enter__()
        except Exception as exc:
            raise TTSError(f"OpenAI speech-compatible request failed: {exc}") from exc

        # Leaving the response closes it, on every path; a failing close never
        # hides the utterance's own error.
        with cleanup(
            lambda: response_cm.__exit__(None, None, None),
            "OpenAI speech-compatible response",
        ):
            try:
                chunks = response.iter_bytes()
            except Exception as exc:
                raise TTSError(
                    f"OpenAI speech-compatible request failed: {exc}"
                ) from exc

            # Whole samples in 100 ms slices (an odd trailing byte waits for
            # the next chunk); `head` holds the first bytes until the WAV
            # check has seen four.
            feeder = PCMFeeder(callback, stop, slice_bytes=self._slice_bytes)
            head = b""
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
                    raise TTSError(
                        f"OpenAI speech-compatible request failed: {exc}"
                    ) from exc
                if not checked:
                    head += chunk
                    if len(head) < len(_WAV_MAGIC):
                        continue
                    if head.startswith(_WAV_MAGIC):
                        raise TTSError(
                            "OpenAI-compatible server returned WAV, not raw pcm"
                        )
                    checked = True
                    chunk, head = head, b""
                if not feeder.feed(chunk):
                    return

            # A body shorter than the WAV check: feed its whole samples.
            if not checked:
                feeder.feed(head)

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
