"""Gemini TTS module.

Behind the `gemini` extra: `google.genai` is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

`generate_content_stream` with an `AUDIO` response streams headerless 16-bit
little-endian PCM at 24 kHz in small parts, fed to the callback unchanged. It
takes one complete prompt per request, so a text stream is grouped into
sentences (see specs/modules/gemini.md).
"""

import logging
import math
import os
import threading
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from typing import Any, cast

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
_DEFAULT_MODEL = "gemini-3.8-flash-tts"
_DEFAULT_TIMEOUT = 30.0
_MIME = "audio/l16"


def _optional_str(config: dict, key: str) -> str | None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ConfigError(f"Gemini module '{key}' must be a non-empty string")
    return value


def _check_mime(mime: str | None) -> None:
    """Reject anything but mono 24 kHz L16: playing another format would be
    noise. Parameters the API omits are taken at their documented values."""
    fields = [field.strip().lower() for field in (mime or "").split(";")]
    params = dict(field.split("=", 1) for field in fields[1:] if "=" in field)
    if (
        fields[0] != _MIME
        or params.get("rate", str(_SAMPLE_RATE)) != str(_SAMPLE_RATE)
        or params.get("channels", "1") != "1"
    ):
        raise TTSError(f"Gemini returned unexpected audio format: {mime!r}")


def _reason_name(reason: object) -> str:
    return str(getattr(reason, "value", reason))


class GeminiModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            # Not `from google import genai`: that binds None instead of raising
            # when sys.modules["google.genai"] is None (see the spec).
            import google.genai as genai  # noqa: PLR0402
        except ImportError as exc:
            raise ConfigError(
                "The 'gemini' module requires the gemini extra: "
                "pip install tts-engine[gemini]"
            ) from exc

        api_key = self._resolve_api_key(config)

        model = config.get("model", _DEFAULT_MODEL)
        if not isinstance(model, str) or not model:
            raise ConfigError("Gemini module 'model' must be a non-empty string")

        voice = _optional_str(config, "voice")
        language_code = _optional_str(config, "language_code")

        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ConfigError("Gemini module 'timeout' must be a finite number > 0")

        # Only the fields that were set: the API's own defaults apply to the rest.
        speech_config: dict = {}
        if voice is not None:
            speech_config["voice_config"] = {
                "prebuilt_voice_config": {"voice_name": voice}
            }
        if language_code is not None:
            speech_config["language_code"] = language_code
        # A plain dict (the SDK's GenerateContentConfigDict shape), so the module
        # never imports google.genai.types.
        self._config: Any = {"response_modalities": ["AUDIO"]}
        if speech_config:
            self._config["speech_config"] = speech_config
        self._model = model

        # `timeout` (milliseconds here) bounds the wait for the response and
        # for each read of the stream, so a stalled request fails instead of
        # holding the worker (and a cancel) indefinitely.
        self._client = genai.Client(
            api_key=api_key, http_options={"timeout": math.ceil(timeout * 1000)}
        )

    def close(self) -> None:
        # Closes the SDK's pooled HTTP connections; the module is done.
        self._client.close()

    @property
    def sample_rate(self) -> int:
        # Gemini TTS audio is fixed at 24 kHz; the API has no rate option.
        return _SAMPLE_RATE

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env``."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("Gemini module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("Gemini module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"Gemini module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "Gemini module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _generate(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Synthesize one text and feed its PCM, polling `stop`. Runs on the
        worker thread."""
        try:
            # Annotated as an Iterator, but a generator function: `close` exists.
            chunks = cast(
                Generator[Any, None, None],
                self._client.models.generate_content_stream(
                    model=self._model, contents=text, config=self._config
                ),
            )
        except Exception as exc:
            raise TTSError(f"Gemini request failed: {exc}") from exc

        carry = b""  # a sample split across two parts
        # Closing the generator drops the HTTP connection, on every path; a
        # failing close never hides the utterance's own error.
        with cleanup(chunks.close, "Gemini response"):
            while not stop.is_set():
                # Advance inside the provider try so SDK/transport errors become
                # TTSError; feed outside it so a playback failure keeps its own
                # identity. The request itself is sent on the first `next`.
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"Gemini request failed: {exc}") from exc

                feedback = chunk.prompt_feedback
                if feedback is not None and feedback.block_reason:
                    raise TTSError(
                        "Gemini blocked the prompt: "
                        f"{_reason_name(feedback.block_reason)}"
                    )
                for candidate in chunk.candidates or []:
                    content = candidate.content
                    for part in (content.parts if content else None) or []:
                        blob = part.inline_data
                        if blob is None or not blob.data:
                            continue  # e.g. a text part
                        _check_mime(blob.mime_type)
                        data = carry + blob.data
                        whole = len(data) - len(data) % 2
                        data, carry = data[:whole], data[whole:]
                        if stop.is_set():
                            return
                        if data:
                            callback(data)
                    reason = candidate.finish_reason
                    if reason is not None and _reason_name(reason) != "STOP":
                        raise TTSError(
                            f"Gemini stopped synthesis early: {_reason_name(reason)}"
                        )

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
        # generateContent takes one complete prompt per request: group the
        # pieces into sentences as they arrive and request each, in one worker.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            for sentence in segment_sentences(pieces):
                if stop.is_set():
                    return
                self._generate(sentence, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
