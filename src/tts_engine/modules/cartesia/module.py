"""Cartesia Sonic streaming TTS module.

Behind the `cartesia` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

Both input shapes run over the SDK's sync TTS WebSocket (`websocket_connect`),
whose contexts take text incrementally: a complete text is one piece, a text
stream is forwarded as it arrives, so this is a true stream-to-stream pass. A
sender thread pushes word groups while the worker polls the socket with a
short timeout, so it can honor `stop` while the server is silent.

The server expires a context ~7 s after its last audio and then drops further
text silently, so the sender moves to a fresh context on the same socket once
the utterance has been idle for `rotate_after` (see specs/modules/cartesia.md,
"Context expiry and rotation"). Every context is cancelled before the socket
closes, which makes the close prompt.
"""

import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
    run_text_stream_worker,
    split_at_last_space,
)

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "sonic-3.6"
_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
_SPEED_RANGE = (0.6, 1.5)
_VOLUME_RANGE = (0.5, 2.0)
_BUFFER_DELAY_RANGE = (0, 5000)
# How often the worker wakes from `recv_bytes` to poll `stop`.
_POLL_SECONDS = 0.1
# A context is left behind once the utterance has been idle (no push, no
# audio) this long: any text the server held has been released by then
# (after at most max_buffer_delay), and it is still under the ~7 s expiry.
_ROTATE_MIN = 4.0
_EXPIRY_MARGIN = 1.0
_DEFAULT_BUFFER_DELAY = 3.0  # the API's max_buffer_delay_ms default, in seconds


def _optional_str(config: dict, field: str) -> str | None:
    """Read an optional field that, when present, must be a non-empty string."""
    value = config.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"Cartesia module '{field}' must be a non-empty string")
    return value


def _optional_number(config: dict, field: str, lo: float, hi: float) -> float | None:
    """Read an optional numeric field that must lie in [lo, hi]; bools rejected."""
    value = config.get(field)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (lo <= value <= hi)
    ):
        raise ConfigError(
            f"Cartesia module '{field}' must be a number between {lo} and {hi}"
        )
    return float(value)


class _Session:
    """State shared by the sender and the worker of one utterance."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()  # last push or audio chunk
        self.contexts: list[Any] = []  # every context opened, to cancel at the end
        self.current_id: str | None = None
        self.current: Any = None
        self.pushed = False  # the current context has received text
        self.expired: set[str] = set()  # contexts the server ended early
        self.final_id: str | None = None  # the context ended by no_more_inputs
        self.sender_done = False
        self.sender_errors: list[Exception] = []


class CartesiaModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            import websockets.sync.client  # noqa: F401  (the SDK's [websockets] extra)
            from cartesia import Cartesia
        except ImportError as exc:
            raise ConfigError(
                "The 'cartesia' module requires the cartesia extra: "
                "pip install tts-engine[cartesia]"
            ) from exc

        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("Cartesia module requires a non-empty 'voice_id'")

        model = config.get("model", _DEFAULT_MODEL)
        if not isinstance(model, str) or not model:
            raise ConfigError("Cartesia module 'model' must be a non-empty string")

        sample_rate = config.get("sample_rate", 24000)
        if isinstance(sample_rate, bool) or sample_rate not in _SAMPLE_RATES:
            raise ConfigError(
                "Cartesia module 'sample_rate' must be one of "
                + ", ".join(str(r) for r in _SAMPLE_RATES)
            )

        context_kwargs: dict[str, Any] = {
            "model_id": model,
            "voice": voice_id,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate,
            },
        }
        language = _optional_str(config, "language")
        if language is not None:
            context_kwargs["language"] = language

        generation_config: dict[str, Any] = {}
        speed = _optional_number(config, "speed", *_SPEED_RANGE)
        if speed is not None:
            generation_config["speed"] = speed
        volume = _optional_number(config, "volume", *_VOLUME_RANGE)
        if volume is not None:
            generation_config["volume"] = volume
        emotion = _optional_str(config, "emotion")
        if emotion is not None:
            generation_config["emotion"] = emotion
        if generation_config:
            context_kwargs["generation_config"] = generation_config

        buffer_delay = _DEFAULT_BUFFER_DELAY
        max_buffer_delay_ms = config.get("max_buffer_delay_ms")
        if max_buffer_delay_ms is not None:
            lo, hi = _BUFFER_DELAY_RANGE
            if (
                isinstance(max_buffer_delay_ms, bool)
                or not isinstance(max_buffer_delay_ms, int)
                or not (lo <= max_buffer_delay_ms <= hi)
            ):
                raise ConfigError(
                    "Cartesia module 'max_buffer_delay_ms' must be an integer "
                    f"between {lo} and {hi}"
                )
            context_kwargs["max_buffer_delay_ms"] = max_buffer_delay_ms
            buffer_delay = max_buffer_delay_ms / 1000

        self._sample_rate: int = sample_rate
        self._context_kwargs = context_kwargs
        self._rotate_after = max(_ROTATE_MIN, buffer_delay + _EXPIRY_MARGIN)
        # The key is always passed explicitly, so this module's key rules are
        # the only ones in force.
        self._client = Cartesia(api_key=api_key)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env`` — so a config file can be
        committed with only the env-var name and no secret."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("Cartesia module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("Cartesia module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"Cartesia module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "Cartesia module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _stale(self, session: _Session) -> bool:
        """Whether the current context must be left behind (caller holds the
        lock): none yet, ended early by the server, or idle past rotate_after."""
        if session.current is None or session.current_id in session.expired:
            return True
        return (
            session.pushed
            and time.monotonic() - session.last_activity >= self._rotate_after
        )

    def _send(
        self,
        ws,
        pieces: Iterator[str],
        session: _Session,
        stop: threading.Event,
    ) -> None:
        """Sender thread: push `pieces` grouped at word boundaries, moving to a
        fresh context when the current one is stale, then end the last context
        with no_more_inputs. Sends nothing more once `stop` is set, and never
        an empty transcript (the API rejects one)."""

        def push(text: str) -> None:
            with session.lock:
                if self._stale(session):
                    context_id = str(uuid.uuid4())
                    ctx = ws.context(context_id=context_id, **self._context_kwargs)
                    session.contexts.append(ctx)
                    session.current, session.current_id = ctx, context_id
                    session.pushed = False
                ctx = session.current
            ctx.push(text)
            with session.lock:
                session.pushed = True
                session.last_activity = time.monotonic()

        try:
            buffer = ""
            for piece in pieces:  # ends at end-of-stream or on `stop`
                buffer += piece
                words, buffer = split_at_last_space(buffer)
                if words.strip():
                    push(words)
            if stop.is_set():
                return  # aborted: the worker cancels and closes
            if buffer.strip():
                push(buffer)
            with session.lock:
                # A stale context has spoken everything: nothing to end. An
                # empty stream has no context at all.
                ctx = None if self._stale(session) else session.current
                if ctx is not None:
                    session.final_id = session.current_id
            if ctx is not None and not stop.is_set():
                ctx.no_more_inputs()
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            if not stop.is_set():
                with session.lock:
                    session.sender_errors.append(exc)
        finally:
            with session.lock:
                session.sender_done = True

    def _synthesize(
        self,
        pieces: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Run one utterance over one socket on the worker thread: start the
        sender, then feed audio until the final context is done, the sender
        finishes with nothing left to end, `stop`, or a failure.

        SDK/socket failures become TTSError; `callback` runs outside that
        boundary so a downstream playback failure keeps its own identity.
        """
        try:
            # No receive flow control: while `callback` blocks on playback,
            # websockets keeps reading the socket instead of pausing at 16
            # unread frames, so the server's close frame gets through and an
            # aborted utterance closes at once rather than after the 10 s
            # close timeout. The whole utterance's audio is at most a few MB.
            connection = self._client.tts.websocket_connect(
                websocket_connection_options={"max_queue": None}
            )
            ws = connection.__enter__()
        except Exception as exc:
            raise TTSError(f"Cartesia request failed: {exc}") from exc

        session = _Session()
        try:
            # Daemon: a sender blocked on text after the worker has left is
            # released when `stop` or end-of-stream reaches `pieces`; its late
            # send on the closed socket is then ignored.
            threading.Thread(
                target=self._send,
                args=(ws, pieces, session, stop),
                name="cartesia-sender",
                daemon=True,
            ).start()
            self._receive(ws, session, callback, stop)
        finally:
            # Release a sender still blocked on text.
            stop.set()
            with session.lock:
                contexts = list(session.contexts)
            # Stop any generation still running for this utterance.
            for ctx in contexts:
                try:
                    ctx.cancel()
                except Exception:  # a socket already broken
                    log.debug("Cartesia context cancel failed", exc_info=True)
            # The server often holds the close handshake for ~1 s, even after
            # `done`. Callbacks have stopped, so close off the worker thread
            # and return now.
            threading.Thread(
                target=self._close,
                args=(connection,),
                name="cartesia-closer",
                daemon=True,
            ).start()

    @staticmethod
    def _close(connection) -> None:
        try:
            connection.__exit__(None, None, None)
        except Exception:  # closing a socket already broken
            log.debug("Cartesia socket close failed", exc_info=True)

    @staticmethod
    def _receive(
        ws,
        session: _Session,
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        carry = b""  # an odd trailing byte, completed by the next chunk
        while not stop.is_set():
            with session.lock:
                sender_error = (
                    session.sender_errors[0] if session.sender_errors else None
                )
                finished = session.sender_done and session.final_id is None
            if sender_error is not None:
                raise TTSError(
                    f"Cartesia request failed: {sender_error}"
                ) from sender_error
            if finished:
                return  # nothing left to end: every context has spoken
            try:
                event = ws.parse_event(ws.recv_bytes(timeout=_POLL_SECONDS))
                kind = getattr(event, "type", None)
                context_id = getattr(event, "context_id", None)
                audio = event.audio if kind == "chunk" else None
            except TimeoutError:
                continue
            except Exception as exc:
                raise TTSError(f"Cartesia request failed: {exc}") from exc

            if kind == "chunk":
                with session.lock:
                    session.last_activity = time.monotonic()
                if audio:
                    data = carry + audio
                    whole = len(data) - len(data) % 2
                    carry = data[whole:]
                    if whole:
                        callback(data[:whole])
            elif kind == "done":
                with session.lock:
                    if context_id is not None and context_id == session.final_id:
                        return  # all audio of the last context is in
                    if context_id is not None:
                        session.expired.add(context_id)
            elif kind == "error":
                with session.lock:
                    # Expired, or no longer the context being spoken into.
                    left_behind = context_id is not None and (
                        context_id in session.expired
                        or context_id not in (session.current_id, session.final_id)
                    )
                if left_behind:
                    log.debug(
                        "Cartesia error on a left-behind context: %s",
                        getattr(event, "message", ""),
                    )
                    continue
                raise TTSError(
                    "Cartesia request failed: "
                    f"{getattr(event, 'title', None)}: {getattr(event, 'message', None)} "
                    f"(status {getattr(event, 'status_code', None)})"
                )

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(stop: threading.Event) -> None:
            self._synthesize(iter([text]), callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._synthesize(pieces, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
