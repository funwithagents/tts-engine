"""ElevenLabs streaming TTS module.

Behind the `elevenlabs` extra: `elevenlabs`, `miniaudio` and `websockets` (a
dependency of the SDK) are imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require them — a missing extra
becomes a clear `ConfigError`.

Both input shapes share one MP3 -> PCM decode-and-feed loop; only the raw MP3
source differs: the SDK's `text_to_speech.stream` (HTTPS, every model) for a
complete text, and the module's own loop over a stream-input WebSocket for a
text stream — a sender thread forwarding text as it arrives while the worker
receives audio continuously. Which socket depends on the model: the v2 family
is served by the text-to-speech socket, v3/v4 models only by the
text-to-dialogue one; the two differ in their frames, not in the loop, so a
`_SocketProtocol` picked at construction carries the difference (see
specs/modules/elevenlabs.md, "Two sockets" and "Text stream").
"""

import base64
import itertools
import json
import math
import threading
import time
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    cleanup,
    finite_number,
    normalize_gender,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    split_at_last_space,
)

_OUTPUT_FORMAT = "mp3_44100_128"
_DEFAULT_TIMEOUT = 30.0
_LABEL = "ElevenLabs module"
_STREAM_INPUT_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
_DIALOGUE_STREAM_INPUT_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
# Models the text-to-speech socket rejects (HTTP 400 at the handshake) and the
# text-to-dialogue socket serves — ElevenLabs' own rule for that socket:
# `model_id` "must start with eleven_v3 or eleven_v4".
_DIALOGUE_MODEL_PREFIXES = ("eleven_v3", "eleven_v4")
# The TTS socket's minimum buffered-text size before it starts generating.
_CHUNK_LENGTH_SCHEDULE = [50]
# How often the receive loop wakes to poll `stop` while the server is silent
# (a paused text source), so a cancel is honored promptly.
_RECV_POLL_SECONDS = 0.1
# The dialogue socket drops a connection after 20 s without a client frame; the
# receiver sends `keep_alive` once this long has passed since the last one, so
# a paused text source (an LLM mid-answer) does not lose the socket.
_KEEP_ALIVE_SECONDS = 10.0


@dataclass(frozen=True)
class _SocketProtocol:
    """The frames of one stream-input socket; the sender/receiver loop is the
    same for both, only what it sends and what ends it differ."""

    url: str
    opening_frame: dict  # sent once, right after the handshake
    text_frame: Callable[[str], dict]  # one group of words
    end_frame: dict  # end of stream: generate the rest, then the final message
    final_key: str  # the server's end-of-audio flag
    keep_alive_frame: dict | None  # sent while idle, or None when not needed


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


def _api_error(exc: Exception) -> str:
    """An SDK `ApiError` as its status and the API's own message (its string
    form starts with every response header), anything else as is."""
    status, body = getattr(exc, "status_code", None), getattr(exc, "body", None)
    if status is None:
        return str(exc)
    detail = body.get("detail", body) if isinstance(body, dict) else body
    message = detail.get("message", detail) if isinstance(detail, dict) else detail
    return f"HTTP {status}: {message}"


class ElevenLabsModule(TTSModule):
    kind = "hosted"

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

        api_key = resolve_api_key(config, label=_LABEL)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("ElevenLabs module requires a non-empty 'voice_id'")

        model = optional_str(config, "model", label=_LABEL, default="eleven_flash_v2_5")

        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )

        self._api_key: str = api_key
        self._voice_id: str = voice_id
        self._model: str = model
        self._timeout: float = float(timeout)
        self._stability: float = finite_number(
            config, "stability", label=_LABEL, default=0.5, minimum=0.0, maximum=1.0
        )
        self._similarity_boost: float = finite_number(
            config,
            "similarity_boost",
            label=_LABEL,
            default=0.75,
            minimum=0.0,
            maximum=1.0,
        )
        self._miniaudio = miniaudio
        self._voice_settings_cls = VoiceSettings
        self._chunk_source_cls = _make_chunk_source_class(miniaudio)
        self._ws_connect = connect
        self._ws_closed_ok = ConnectionClosedOK
        self._client = ElevenLabs(api_key=api_key)
        self._protocol = (
            self._dialogue_protocol()
            if model.startswith(_DIALOGUE_MODEL_PREFIXES)
            else self._tts_protocol()
        )

    def _tts_protocol(self) -> _SocketProtocol:
        """The text-to-speech stream-input socket (the v2 family): the voice is
        in the URL, text goes as `text`, an empty `text` ends the stream."""
        voice_id = self._voice_id
        return _SocketProtocol(
            url=_STREAM_INPUT_URL.format(voice_id=urllib.parse.quote(voice_id, safe=""))
            + "?"
            + urllib.parse.urlencode(
                {"model_id": self._model, "output_format": _OUTPUT_FORMAT}
            ),
            opening_frame={
                "text": " ",
                "voice_settings": {
                    "stability": self._stability,
                    "similarity_boost": self._similarity_boost,
                },
                "generation_config": {"chunk_length_schedule": _CHUNK_LENGTH_SCHEDULE},
            },
            text_frame=lambda words: {"text": words, "try_trigger_generation": True},
            end_frame={"text": ""},
            final_key="isFinal",
            keep_alive_frame=None,  # the socket's inactivity timeout is generous
        )

    def _dialogue_protocol(self) -> _SocketProtocol:
        """The text-to-dialogue stream-input socket (v3/v4 models): the voice is
        registered in the opening frame and named on every input, text goes as
        `inputs`, `close_socket` ends the stream, and the server needs a frame
        every 20 s."""
        voice_id = self._voice_id
        return _SocketProtocol(
            url=_DIALOGUE_STREAM_INPUT_URL
            + "?"
            + urllib.parse.urlencode(
                {"model_id": self._model, "output_format": _OUTPUT_FORMAT}
            ),
            opening_frame={
                "voices": [voice_id],
                "settings": {
                    "stability": self._stability,
                    "similarity": self._similarity_boost,
                },
            },
            text_frame=lambda words: {
                "inputs": [{"text": words, "voice_id": voice_id}]
            },
            end_frame={"close_socket": True},
            final_key="is_final",
            keep_alive_frame={"keep_alive": True},
        )

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The premade voices and the account's own, from `voices.get_all()`
        (the key needs the `voices_read` permission). The voice's `labels`
        carry its gender and age; the other labels become tags."""
        try:
            from elevenlabs import ElevenLabs
        except ImportError as exc:
            raise ConfigError(
                "The 'elevenlabs' module requires the elevenlabs extra: "
                "pip install tts-engine[elevenlabs]"
            ) from exc
        api_key = resolve_api_key(config, label=_LABEL)
        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )
        try:
            response = ElevenLabs(api_key=api_key).voices.get_all(
                request_options={"timeout_in_seconds": math.ceil(timeout)}
            )
        except Exception as exc:
            raise TTSError(
                f"{_LABEL}: voice listing failed: {_api_error(exc)}"
            ) from exc

        voices: list[VoiceInfo] = []
        for voice in response.voices:
            labels = dict(voice.labels or {})
            languages = tuple(
                dict.fromkeys(
                    language.locale or language.language
                    for language in voice.verified_languages or ()
                )
            )
            if not languages and labels.get("language"):
                languages = (labels["language"],)
            voices.append(
                VoiceInfo(
                    id=voice.voice_id,
                    name=voice.name or voice.voice_id,
                    config={"voice_id": voice.voice_id},
                    languages=languages,
                    gender=normalize_gender(labels.get("gender")),
                    age=labels.get("age") or None,
                    description=voice.description or None,
                    tags=tuple(
                        value
                        for key, value in labels.items()
                        if key not in ("gender", "age", "language") and value
                    ),
                )
            )
        return voices

    @property
    def sample_rate(self) -> int:
        # Matches the requested output_format="mp3_44100_128" and the rate
        # miniaudio decodes to below.
        return 44100

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
        """Yield MP3 chunks from the model's stream-input WebSocket for a text
        stream.

        A sender thread forwards `pieces` as they arrive (grouped at word
        boundaries, then the end-of-stream frame); this generator, on the
        worker thread, receives audio continuously, polling `stop` while the
        server is silent and keeping the socket alive when the protocol needs
        it. Runs until the server's final message, `stop`, or a failure on
        either side.
        """
        protocol = self._protocol
        sender_errors: list[Exception] = []
        # Set once the sender has finished without ever sending text (an empty
        # stream): nothing was requested, so the receiver has nothing to wait
        # for and drops the connection without an end-of-stream.
        nothing_sent = threading.Event()
        # When the last client frame went out, for the idle keep-alive. Read
        # and written from both threads; a float store is atomic under the GIL
        # and a stale read only sends one keep-alive early.
        last_sent = [time.monotonic()]

        def send_frame(frame: dict) -> None:
            ws.send(json.dumps(frame))
            last_sent[0] = time.monotonic()

        with self._ws_connect(
            protocol.url,
            additional_headers={"xi-api-key": self._api_key},
            open_timeout=self._timeout,
        ) as ws:
            send_frame(protocol.opening_frame)

            def send_all() -> None:
                buffer = ""
                sent = False
                try:
                    for piece in pieces:  # ends at end-of-stream or on `stop`
                        buffer += piece
                        words, buffer = split_at_last_space(buffer)
                        if words:
                            send_frame(protocol.text_frame(words))
                            sent = True
                    if stop.is_set():
                        return  # aborted: no end-of-stream, the request is dropped
                    if buffer:
                        send_frame(protocol.text_frame(buffer))
                        sent = True
                    if not sent:
                        nothing_sent.set()  # empty stream: no request to finish
                        return
                    send_frame(protocol.end_frame)
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
                    if (
                        protocol.keep_alive_frame is not None
                        and time.monotonic() - last_sent[0] >= _KEEP_ALIVE_SECONDS
                    ):
                        # Silent on both sides (the text source paused): keep
                        # the server from dropping the connection. Sends are
                        # mutex-guarded in websockets.sync, so this is safe
                        # beside the sender thread.
                        send_frame(protocol.keep_alive_frame)
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
                # Frames without audio or the final flag (the dialogue
                # socket's `is_final_audio_for_turn`) are informational.
                audio = message.get("audio")
                if audio:
                    yield base64.b64decode(audio)
                if message.get(protocol.final_key):
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
