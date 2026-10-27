"""ElevenLabs streaming TTS module.

Behind the `elevenlabs` extra: `elevenlabs`, `miniaudio` and `websockets` (a
dependency of the SDK) are imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require them — a missing extra
becomes a clear `ConfigError`.

Both input shapes share one MP3 -> PCM decode-and-feed loop; only the raw MP3
source differs: the SDK's `text_to_speech.stream` (HTTPS) for a complete text,
and the module's own loop over the stream-input WebSocket for a text stream —
a sender thread forwarding text as it arrives while the worker receives audio
continuously (see specs/modules/elevenlabs.md, "Text stream").
"""

import base64
import itertools
import json
import math
import os
import threading
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable, Iterator

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    cleanup,
    run_cancellable_worker,
    run_text_stream_worker,
    split_at_last_space,
)

_OUTPUT_FORMAT = "mp3_44100_128"
_DEFAULT_TIMEOUT = 30.0
_STREAM_INPUT_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
# The API's minimum buffered-text size before it starts generating.
_CHUNK_LENGTH_SCHEDULE = [50]
# How often the receive loop wakes to poll `stop` while the server is silent
# (a paused text source), so a cancel is honored promptly.
_RECV_POLL_SECONDS = 0.1


def _make_chunk_source_class(miniaudio_mod):
    """Build the chunk-source adapter from the lazily imported miniaudio module
    (its base class can't be named at file top without importing the extra)."""

    class _ChunkSource(miniaudio_mod.StreamableSource):
        """Wraps a bytes-chunk iterator as a miniaudio StreamableSource."""

        def __init__(self, chunks: Iterator[bytes]) -> None:
            self._chunks = chunks
            self._buf = bytearray()

        def read(self, num_bytes: int) -> bytes:
            # Short reads on purpose: serve whatever is buffered (pulling one
            # provider chunk when empty) instead of blocking until `num_bytes`
            # have arrived. The decoder asks for ~64 KB at a time, about 4 s of
            # 128 kbps MP3, so filling the request would delay the first PCM
            # chunk by that much audio. Only an empty return means end of
            # stream.
            while not self._buf:
                try:
                    self._buf.extend(next(self._chunks))
                except StopIteration:
                    return b""
            data = bytes(self._buf[:num_bytes])
            del self._buf[:num_bytes]
            return data

    return _ChunkSource


def _unit_interval(config: dict, field: str, default: float) -> float:
    """Read an optional numeric field that must lie in [0, 1]; bools rejected."""
    value = config.get(field, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (0.0 <= value <= 1.0)
    ):
        raise ConfigError(
            f"ElevenLabs module '{field}' must be a number between 0 and 1"
        )
    return float(value)


class ElevenLabsModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            import miniaudio
            from elevenlabs import ElevenLabs
            from elevenlabs.types import VoiceSettings
            from websockets.exceptions import ConnectionClosedOK
            from websockets.sync.client import connect
        except ImportError as exc:
            raise ConfigError(
                "The 'elevenlabs' module requires the elevenlabs extra: "
                "pip install tts-engine[elevenlabs]"
            ) from exc

        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("ElevenLabs module requires a non-empty 'voice_id'")

        model = config.get("model", "eleven_flash_v2_5")
        if not isinstance(model, str) or not model:
            raise ConfigError("ElevenLabs module 'model' must be a non-empty string")

        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ConfigError("ElevenLabs module 'timeout' must be a finite number > 0")

        self._api_key: str = api_key
        self._voice_id: str = voice_id
        self._model: str = model
        self._timeout: float = float(timeout)
        self._stability: float = _unit_interval(config, "stability", 0.5)
        self._similarity_boost: float = _unit_interval(config, "similarity_boost", 0.75)
        self._miniaudio = miniaudio
        self._voice_settings_cls = VoiceSettings
        self._chunk_source_cls = _make_chunk_source_class(miniaudio)
        self._ws_connect = connect
        self._ws_closed_ok = ConnectionClosedOK
        self._client = ElevenLabs(api_key=api_key)

    @property
    def sample_rate(self) -> int:
        # Matches the requested output_format="mp3_44100_128" and the rate
        # miniaudio decodes to below.
        return 44100

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env`` — so a config file can be
        committed with only the env-var name and no secret."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("ElevenLabs module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("ElevenLabs module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"ElevenLabs module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "ElevenLabs module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _decode_and_feed(
        self,
        open_raw: Callable[[], Iterable[bytes]],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Open the raw MP3 chunk source, decode it to PCM and feed `callback`,
        polling `stop` between chunks. Runs on the worker thread."""
        # Build the provider request inside the provider try so SDK failures
        # become TTSError.
        try:
            raw = open_raw()
        except Exception as exc:
            raise TTSError(f"ElevenLabs request failed: {exc}") from exc
        # Release the raw source (a generator holding an HTTP response or the
        # stream-input socket) when done, not whenever it is collected; a
        # decoder failure releases it too, and a failing release never hides
        # the utterance's own error.
        with cleanup(getattr(raw, "close", lambda: None), "ElevenLabs raw source"):
            try:
                raw_chunks = (chunk for chunk in raw if chunk)
                first = next(raw_chunks, None)
                if first is None:
                    return  # no audio came (e.g. an empty text stream): nothing to decode
                pcm_iter = iter(
                    self._miniaudio.stream_any(
                        self._chunk_source_cls(itertools.chain([first], raw_chunks)),
                        output_format=self._miniaudio.SampleFormat.SIGNED16,
                        nchannels=1,
                        sample_rate=44100,
                    )
                )
            except TTSError:
                raise  # already labeled by the raw source (a protocol error)
            except Exception as exc:
                raise TTSError(f"ElevenLabs request failed: {exc}") from exc

            while not stop.is_set():
                # Advance the decoder inside the provider try so SDK/decoder
                # errors become TTSError; feed the callback outside it so a
                # downstream playback failure keeps its own identity.
                try:
                    pcm_chunk = next(pcm_iter)
                except StopIteration:
                    break
                except TTSError:
                    raise  # already labeled by the raw source (a protocol error)
                except Exception as exc:
                    raise TTSError(f"ElevenLabs request failed: {exc}") from exc
                callback(pcm_chunk.tobytes())

    def _stream_input_chunks(
        self, pieces: Iterator[str], stop: threading.Event
    ) -> Iterator[bytes]:
        """Yield MP3 chunks from the stream-input WebSocket for a text stream.

        A sender thread forwards `pieces` as they arrive (grouped at word
        boundaries, then the end-of-stream message); this generator, on the
        worker thread, receives audio continuously, polling `stop` while the
        server is silent. Runs until the server's final message, `stop`, or a
        failure on either side.
        """
        url = (
            _STREAM_INPUT_URL.format(
                voice_id=urllib.parse.quote(self._voice_id, safe="")
            )
            + "?"
            + urllib.parse.urlencode(
                {"model_id": self._model, "output_format": _OUTPUT_FORMAT}
            )
        )
        sender_errors: list[Exception] = []
        # Set once the sender has finished without ever sending text (an empty
        # stream): nothing was requested, so the receiver has nothing to wait
        # for and drops the connection without an end-of-stream.
        nothing_sent = threading.Event()

        with self._ws_connect(
            url,
            additional_headers={"xi-api-key": self._api_key},
            open_timeout=self._timeout,
        ) as ws:
            ws.send(
                json.dumps(
                    {
                        "text": " ",
                        "voice_settings": {
                            "stability": self._stability,
                            "similarity_boost": self._similarity_boost,
                        },
                        "generation_config": {
                            "chunk_length_schedule": _CHUNK_LENGTH_SCHEDULE
                        },
                    }
                )
            )

            def send_all() -> None:
                buffer = ""
                sent = False
                try:
                    for piece in pieces:  # ends at end-of-stream or on `stop`
                        buffer += piece
                        words, buffer = split_at_last_space(buffer)
                        if words:
                            ws.send(
                                json.dumps(
                                    {"text": words, "try_trigger_generation": True}
                                )
                            )
                            sent = True
                    if stop.is_set():
                        return  # aborted: no end-of-stream, the request is dropped
                    if buffer:
                        ws.send(
                            json.dumps({"text": buffer, "try_trigger_generation": True})
                        )
                        sent = True
                    if not sent:
                        nothing_sent.set()  # empty stream: no request to finish
                        return
                    ws.send(json.dumps({"text": ""}))
                except Exception as exc:  # noqa: BLE001  (surfaced by the receiver)
                    if not stop.is_set():
                        sender_errors.append(exc)

            # Daemon: a sender blocked on text after the receiver has left is
            # released when `stop` or end-of-stream reaches `pieces`; its late
            # send on the closed socket is then ignored above.
            sender = threading.Thread(
                target=send_all, name="elevenlabs-stream-input-sender", daemon=True
            )
            sender.start()

            while not stop.is_set():
                if sender_errors:
                    raise sender_errors[0]
                if nothing_sent.is_set():
                    break
                try:
                    raw = ws.recv(timeout=_RECV_POLL_SECONDS)
                except TimeoutError:
                    continue
                except self._ws_closed_ok:
                    # The server closed cleanly without its final message:
                    # the request did not complete (a rejected request, a
                    # quota cut), so what came is not the whole utterance.
                    raise TTSError(
                        "ElevenLabs closed the stream before its final message"
                    ) from None
                message = json.loads(raw)
                if "error" in message:
                    # A protocol error (the API sends `error`, `message` and a
                    # `code` as a text message, usually before closing).
                    raise TTSError(
                        "ElevenLabs request failed: "
                        f"{message.get('message') or message['error']}"
                    )
                audio = message.get("audio")
                if audio:
                    yield base64.b64decode(audio)
                if message.get("isFinal"):
                    break

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(stop: threading.Event) -> None:
            self._decode_and_feed(
                lambda: self._client.text_to_speech.stream(
                    text=text,
                    voice_id=self._voice_id,
                    model_id=self._model,
                    output_format=_OUTPUT_FORMAT,
                    voice_settings=self._voice_settings_cls(
                        stability=self._stability,
                        similarity_boost=self._similarity_boost,
                    ),
                    # Bounds the wait for the response and for each read of
                    # the body, so a stalled request fails instead of holding
                    # the worker (and a cancel) for the SDK's 240 s default.
                    request_options={"timeout_in_seconds": math.ceil(self._timeout)},
                ),
                callback,
                stop,
            )

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._decode_and_feed(
                lambda: self._stream_input_chunks(pieces, stop), callback, stop
            )

        await run_text_stream_worker(text_stream, _worker)
