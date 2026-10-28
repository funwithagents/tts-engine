"""Cartesia Sonic streaming TTS module.

Behind the `cartesia` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

Both input shapes run over the SDK's sync TTS WebSocket (`websocket_connect`),
whose contexts take text incrementally: a complete text is sent in small
slices, a text stream is forwarded as it arrives, so this is a true
stream-to-stream pass. The module opens one socket at construction and holds
it for its lifetime (replaced before an utterance once dead, idle near the
server's 300 s limit, or after a host sleep), so no utterance pays the
handshake. A reader thread per socket routes the server's events by
`context_id` to the utterance that opened the context; per utterance, a sender
pushes text to contexts and the worker polls the routed events so it can honor
`stop` while the server is silent.

A context is the unit of flow control. It takes at most one segment of text
and is then ended with `no_more_inputs`; the next context opens only once the
server has acknowledged the previous one with `done`, and only while the audio
read but not yet fed stays under `_MAX_UNREAD_SECONDS`. The worker ends a
context the same way once the utterance has been idle for `rotate_after`,
ahead of the server's ~7 s expiry (after which pushed text is dropped
silently), so an LLM pause is acknowledged before text resumes (see
specs/modules/cartesia.md, "Contexts: segments, idle rotation and the `done`
acknowledgement"). An utterance cut short cancels its unacknowledged contexts
and leaves the socket open for the next one.
"""

import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, cast

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    TTSOptions,
    cut_at_boundary,
    finite_number,
    open_cancellable,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    slice_bytes_for,
    slice_text,
    split_at_last_space,
)

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "sonic-3.6"
_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
_SPEED_RANGE = (0.6, 1.5)
_VOLUME_RANGE = (0.5, 2.0)
_BUFFER_DELAY_RANGE = (0, 5000)
_DEFAULT_TIMEOUT = 30.0
_LABEL = "Cartesia module"
# A context takes one segment: it is ended at the first sentence end past
# _SEGMENT_SOFT characters, and at a word boundary at _SEGMENT_HARD at the
# latest. The next context opens once this one's `done` has arrived, so the
# server never holds more than one segment of text whose audio is not in.
_SEGMENT_SOFT = 600
_SEGMENT_HARD = 900
# The sender pushes text only while the audio read and not yet fed is under
# this much: the provider runs at most this far ahead of playback, plus the
# audio of the segment already admitted.
_MAX_UNREAD_SECONDS = 10.0
# A complete text is sent in slices of at most this many characters (a few
# seconds of speech, under `rotate_after`), so the admission gate has a grain
# and a gated context never looks idle.
_SLICE_CHARS = 50
# How often the worker and the reader wake to poll `stop` / the socket.
_POLL_SECONDS = 0.1
# A context is ended once the utterance has been idle (no push, no audio)
# this long: any text the server held has been released by then (after at
# most max_buffer_delay), and it is still under the ~7 s expiry.
_ROTATE_MIN = 4.0
_EXPIRY_MARGIN = 1.0
_DEFAULT_BUFFER_DELAY = 3.0  # the API's max_buffer_delay_ms default, in seconds
# The server closes a socket after 300 s with no message either way (pings do
# not count). One idle longer than this at the start of an utterance is
# replaced first, so a push never races that close and the utterance has a
# minute left to send its first text.
_IDLE_REPLACE = 240.0
# A wall clock ahead of the monotonic one (which stops while the host sleeps)
# by more than this since the socket's last message means a sleep long enough
# to miss a keepalive ping: the socket may be half-open, so it is replaced.
_SLEPT = 20.0

_ENDS_SENTENCE = re.compile(r"(?:[.!?…][\"'”’)\]]*|\n)\s*$")


class _Session:
    """State of one utterance, shared by its sender, its worker and the
    socket's reader. Everything below `changed` is read and written under it."""

    def __init__(self) -> None:
        self.inbox: queue.Queue = queue.Queue()  # (event, audio) or an exception
        self.changed = threading.Condition()
        self.unread = 0  # audio bytes in the inbox: read, not yet fed
        self.last_message = time.monotonic()  # when the server last spoke to us
        self.last_activity = time.monotonic()  # last push or audio chunk
        # Every context this utterance opened, by id: the reader routes only
        # their events here, and the unacknowledged ones are cancelled at the end.
        self.opened: dict[str, Any] = {}
        self.current: Any = None
        self.current_id: str | None = None
        self.pushed = False  # the current context has received text
        self.segment_chars = 0  # text pushed to the current context
        self.ended: set[str] = set()  # contexts given no_more_inputs
        self.done: set[str] = set()  # contexts the server answered with `done`
        self.sender_done = False
        self.sender_errors: list[Exception] = []
        self.stalled = False  # the server went silent on a pending context

    def stale(self) -> bool:
        """Whether the next push needs a fresh context: none yet, ended (by
        the sender or the worker), or ended early by the server."""
        return (
            self.current is None
            or self.current_id in self.ended
            or self.current_id in self.done
        )

    def awaiting_done(self) -> bool:
        """The current context was ended and the server has not acknowledged
        it yet: the next context must wait, so audio keeps its order."""
        return (
            self.current_id is not None
            and self.current_id in self.ended
            and self.current_id not in self.done
        )

    def open(self, ctx: Any, context_id: str) -> None:
        self.opened[context_id] = ctx
        self.current, self.current_id = ctx, context_id
        self.pushed = False
        self.segment_chars = 0

    def end_current(self) -> None:
        """Send no_more_inputs on the current context and record it."""
        assert self.current_id is not None
        self.current.no_more_inputs()
        self.ended.add(self.current_id)

    def finished(self) -> bool:
        """The sender is done and every context it ended has been acknowledged."""
        return self.sender_done and self.ended <= self.done

    def unacknowledged(self) -> list[Any]:
        return [ctx for cid, ctx in self.opened.items() if cid not in self.done]


class _Socket:
    """The module's connection: the SDK manager and connection, whether the
    socket still works, when it last carried a message, and the utterance
    (session) using it."""

    def __init__(self, manager, ws) -> None:
        self.manager = manager
        self.ws = ws
        # Set once the reader has ended or a send failed; `error` says why.
        self.dead = threading.Event()
        self.error: Exception | None = None
        # Set once the socket is being closed: the reader stops.
        self.closed = threading.Event()
        self._lock = threading.Lock()
        self.session: _Session | None = None
        self.last_mono = 0.0
        self.last_wall = 0.0
        self.touch()

    def touch(self) -> None:
        """A message went either way: stamp it on both clocks."""
        self.last_mono = time.monotonic()
        self.last_wall = time.time()

    def worn(self) -> str | None:
        """Why this socket should be replaced before an utterance, if it
        should: idle near the server's limit, or the host slept since its
        last message."""
        mono = time.monotonic() - self.last_mono
        wall = time.time() - self.last_wall
        if max(mono, wall) > _IDLE_REPLACE:
            return f"idle {max(mono, wall):.0f} s"
        if wall - mono > _SLEPT:
            return f"host slept ~{wall - mono:.0f} s"
        return None

    def fail(self, exc: Exception) -> None:
        with self._lock:
            if self.error is None:
                self.error = exc
        self.dead.set()

    def attach(self, session: _Session | None) -> None:
        with self._lock:
            self.session = session

    def attached(self) -> _Session | None:
        with self._lock:
            return self.session

    def close_in_background(self) -> None:
        """Close once, on a daemon thread: the server can hold the close
        handshake for ~1 s, which no caller should wait for."""
        with self._lock:
            if self.closed.is_set():
                return
            self.closed.set()
        threading.Thread(
            target=_close,
            args=(self.manager,),
            name="cartesia-closer",
            daemon=True,
        ).start()


def _close(manager) -> None:
    try:
        manager.__exit__(None, None, None)
    except Exception:  # closing a socket already broken
        log.debug("Cartesia socket close failed", exc_info=True)


def _read(socket: _Socket) -> None:
    """The reader thread, living as long as its socket: route each event to
    the attached utterance when it names one of that utterance's contexts
    (counting its audio), drop it otherwise. The socket is always drained, so
    its close handshake gets through and the sender's gate opens without the
    worker. Ends when the socket fails or is closed."""
    ws = socket.ws
    while not socket.closed.is_set():
        try:
            event = ws.parse_event(ws.recv_bytes(timeout=_POLL_SECONDS))
        except TimeoutError:
            continue
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            socket.fail(exc)
            session = socket.attached()
            if session is not None:
                session.inbox.put(exc)
            return
        socket.touch()
        session = socket.attached()
        kind = getattr(event, "type", None)
        context_id = getattr(event, "context_id", None)
        # The sender registers a context before its first push, so none of
        # its events can arrive before it is known here.
        if session is None or (
            context_id is not None and context_id not in session.opened
        ):
            log.debug("Dropping a %s of foreign context %s", kind, context_id)
            continue
        audio = event.audio if kind == "chunk" else None
        with session.changed:
            now = time.monotonic()
            session.last_message = now
            if audio:
                session.last_activity = now
                session.unread += len(audio)
            session.changed.notify_all()
        session.inbox.put((event, audio))


class CartesiaModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            import websockets.sync.client  # noqa: F401  (the SDK's [websockets] extra)
            from cartesia import Cartesia
            from websockets.exceptions import InvalidStatus
        except ImportError as exc:
            raise ConfigError(
                "The 'cartesia' module requires the cartesia extra: "
                "pip install tts-engine[cartesia]"
            ) from exc

        api_key = resolve_api_key(config, label=_LABEL)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("Cartesia module requires a non-empty 'voice_id'")

        model = optional_str(config, "model", label=_LABEL, default=_DEFAULT_MODEL)

        sample_rate = config.get("sample_rate", 24000)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate not in _SAMPLE_RATES
        ):
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
        language = optional_str(config, "language", label=_LABEL)
        if language is not None:
            context_kwargs["language"] = language

        generation_config: dict[str, Any] = {}
        speed = finite_number(
            config,
            "speed",
            label=_LABEL,
            minimum=_SPEED_RANGE[0],
            maximum=_SPEED_RANGE[1],
        )
        if speed is not None:
            generation_config["speed"] = speed
        volume = finite_number(
            config,
            "volume",
            label=_LABEL,
            minimum=_VOLUME_RANGE[0],
            maximum=_VOLUME_RANGE[1],
        )
        if volume is not None:
            generation_config["volume"] = volume
        emotion = optional_str(config, "emotion", label=_LABEL)
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

        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )

        self._sample_rate: int = sample_rate
        self._context_kwargs = context_kwargs
        self._timeout = float(timeout)
        self._max_unread = int(_MAX_UNREAD_SECONDS * 2 * sample_rate)
        self._slice_bytes = slice_bytes_for(sample_rate)
        self._rotate_after = max(_ROTATE_MIN, buffer_delay + _EXPIRY_MARGIN)
        # The key is always passed explicitly, so this module's key rules are
        # the only ones in force.
        self._client = Cartesia(api_key=api_key)
        # The handshake is paid here, not by the first utterance; it is also
        # where the server rejects a bad key.
        self._socket: _Socket | None = None
        try:
            self._socket = self._open(threading.Event())
        except InvalidStatus as exc:
            self._client.close()
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise ConfigError(
                    f"{_LABEL}: the server rejected the key (HTTP {status})"
                ) from exc
            raise TTSError(f"Cartesia request failed: {exc}") from exc
        except Exception as exc:
            self._client.close()
            raise TTSError(f"Cartesia request failed: {exc}") from exc
        except BaseException:
            self._client.close()
            raise

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            socket.close_in_background()
        self._client.close()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _open(self, stop: threading.Event) -> _Socket | None:
        """Handshake (polling `stop`, bounded by `timeout`), then the socket's
        reader started. `None` once cancelled meanwhile; the connection, if it
        completes later, is closed on the helper thread."""
        # The options are splatted into `websockets.sync.client.connect`,
        # whose `open_timeout` the SDK's TypedDict just does not list, so an
        # abandoned handshake ends on its own after `timeout` too.
        options = cast(Any, {"open_timeout": self._timeout})
        manager = self._client.tts.websocket_connect(
            websocket_connection_options=options
        )
        ws = open_cancellable(
            manager.__enter__,
            lambda _ws: _close(manager),
            stop=stop,
            timeout=self._timeout,
            what="Cartesia socket",
        )
        if ws is None:
            return None
        socket = _Socket(manager, ws)
        threading.Thread(
            target=_read, args=(socket,), name="cartesia-reader", daemon=True
        ).start()
        return socket

    def _ready_socket(self, stop: threading.Event) -> _Socket | None:
        """The held socket, or a fresh one when it is missing, dead, idle near
        the server's limit or possibly half-open after a host sleep; `None`
        once cancelled during a reconnect."""
        socket = self._socket
        if socket is not None:
            if socket.dead.is_set():
                log.debug("Cartesia socket dead, reconnecting: %s", socket.error)
            else:
                reason = socket.worn()
                if reason is None:
                    return socket
                log.debug("Cartesia socket replaced: %s", reason)
            self._socket = None
            socket.close_in_background()
        try:
            self._socket = self._open(stop)
        except Exception as exc:
            raise TTSError(f"Cartesia request failed: {exc}") from exc
        return self._socket

    def _drop(self, socket: _Socket) -> None:
        """Give up a socket that is dead or in an unknown state: the next
        utterance reconnects."""
        if self._socket is socket:
            self._socket = None
        socket.close_in_background()

    @staticmethod
    def _sending(socket: _Socket, send: Callable[[], None]) -> None:
        """One client message: stamp the socket's idle clock; a failure marks
        the socket dead and propagates."""
        try:
            send()
        except Exception as exc:
            socket.fail(exc)
            raise
        socket.touch()

    def _send(
        self,
        socket: _Socket,
        pieces: Iterator[str],
        session: _Session,
        stop: threading.Event,
    ) -> None:
        """Sender thread: push `pieces` grouped at word boundaries into
        contexts of at most one segment each, opening the next context once
        the previous one is acknowledged and the unread audio is under its
        bound, then end the last context. Sends nothing more once `stop` is
        set, and never an empty transcript (the API rejects one)."""
        ws = socket.ws

        def push(text: str) -> None:
            with session.changed:
                while not stop.is_set():
                    # Wait for playback to catch up, and for the server to
                    # acknowledge an ended context before opening the next.
                    if session.unread > self._max_unread or session.awaiting_done():
                        session.changed.wait(_POLL_SECONDS)
                        continue
                    if session.stale():
                        context_id = str(uuid.uuid4())
                        ctx = ws.context(context_id=context_id, **self._context_kwargs)
                        session.open(ctx, context_id)
                    break
                # Every push happens here, under the lock and after this
                # check; the worker sets `stop` before it takes the lock to
                # cancel what is open. So a sender outliving its utterance
                # never pushes into the socket's next one.
                if stop.is_set():
                    return
                # Under the lock, so the worker's idle check cannot end the
                # context between the choice and the push.
                self._sending(socket, lambda: session.current.push(text))
                session.pushed = True
                session.segment_chars += len(text)
                session.last_activity = time.monotonic()
                if session.segment_chars >= _SEGMENT_HARD or (
                    session.segment_chars >= _SEGMENT_SOFT
                    and _ENDS_SENTENCE.search(text)
                ):
                    self._sending(socket, session.end_current)

        def send(text: str) -> None:
            while text and not stop.is_set():
                with session.changed:
                    used = 0 if session.stale() else session.segment_chars
                room = _SEGMENT_HARD - used
                if len(text) <= room:
                    push(text)
                    text = ""
                else:
                    cut = cut_at_boundary(text, room)
                    push(text[:cut])
                    text = text[cut:]

        try:
            buffer = ""
            for piece in pieces:  # ends at end-of-stream or on `stop`
                buffer += piece
                words, buffer = split_at_last_space(buffer)
                if words.strip():
                    send(words)
            if stop.is_set():
                return  # aborted: the worker cancels what is open
            if buffer.strip():
                send(buffer)
            with session.changed:
                # A context already ended (full segment, idle) or expired has
                # nothing to end; an empty stream has no context at all.
                if not stop.is_set() and session.pushed and not session.stale():
                    self._sending(socket, session.end_current)
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            if not stop.is_set():
                with session.changed:
                    session.sender_errors.append(exc)
        finally:
            with session.changed:
                session.sender_done = True
                session.changed.notify_all()

    def _synthesize(
        self,
        pieces: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Run one utterance over the module's socket on the worker thread:
        take the socket (reconnected or replaced first when needed), attach
        this utterance to it, start the sender, then feed audio until every
        ended context is acknowledged and the sender is done, `stop`, or a
        failure. The socket is kept for the next utterance unless it died or
        stalled.

        SDK/socket failures become TTSError; `callback` runs outside that
        boundary so a downstream playback failure keeps its own identity.
        """
        socket = self._ready_socket(stop)
        if socket is None:
            return  # cancelled during the handshake: nothing was sent

        session = _Session()
        socket.attach(session)
        try:
            # Daemon: a sender blocked on text after the worker has left is
            # released when `stop` or end-of-stream reaches `pieces`, and
            # pushes nothing more.
            threading.Thread(
                target=self._send,
                args=(socket, pieces, session, stop),
                name="cartesia-sender",
                daemon=True,
            ).start()
            self._receive(socket, session, callback, stop)
        finally:
            # Release a sender still blocked on text, on an acknowledgement or
            # on the gate; from here on it pushes nothing.
            stop.set()
            with session.changed:
                pending = session.unacknowledged()
            # Stop any generation still running for this utterance. A context
            # already acknowledged is left alone (a cancel of it is a 400).
            for ctx in pending:
                try:
                    self._sending(socket, ctx.cancel)
                except Exception:  # a socket already broken
                    log.debug("Cartesia context cancel failed", exc_info=True)
                    break
            socket.attach(None)
            if session.stalled or socket.dead.is_set():
                self._drop(socket)

    def _receive(
        self,
        socket: _Socket,
        session: _Session,
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        # Whole samples (an odd trailing byte waits for the next chunk) in
        # 100 ms slices.
        feeder = PCMFeeder(callback, stop, slice_bytes=self._slice_bytes)
        while not stop.is_set():
            with session.changed:
                sender_error = (
                    session.sender_errors[0] if session.sender_errors else None
                )
                finished = session.finished()
                self._end_if_idle(socket, session)
            if sender_error is not None:
                raise TTSError(
                    f"Cartesia request failed: {sender_error}"
                ) from sender_error
            if finished:
                return  # every context acknowledged: all audio is in
            try:
                item = session.inbox.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                if socket.dead.is_set() and session.inbox.empty():
                    # Died before this utterance was attached to it.
                    raise TTSError(
                        f"Cartesia request failed: {socket.error}"
                    ) from socket.error
                self._check_stall(session)
                continue
            if isinstance(item, Exception):
                raise TTSError(f"Cartesia request failed: {item}") from item
            event, audio = item
            kind = getattr(event, "type", None)
            context_id = getattr(event, "context_id", None)

            if kind == "chunk":
                if audio:
                    with session.changed:
                        session.unread -= len(audio)
                        session.changed.notify_all()
                    if not feeder.feed(audio):
                        return
            elif kind == "done":
                # Acknowledged (ended by us) or expired (ended by the server):
                # either way the sender may move on.
                if context_id is not None:
                    with session.changed:
                        session.done.add(context_id)
                        session.changed.notify_all()
            elif kind == "error":
                with session.changed:
                    # Expired, or no longer the context being spoken into.
                    left_behind = context_id is not None and (
                        context_id in session.done
                        or (
                            context_id != session.current_id
                            and context_id not in session.ended
                        )
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

    def _end_if_idle(self, socket: _Socket, session: _Session) -> None:
        """Caller holds the lock. End a pushed context idle past rotate_after,
        so its `done` is in hand before the text resumes and the server's
        expiry never swallows a push."""
        if (
            session.current is not None
            and session.pushed
            and not session.stale()
            and time.monotonic() - session.last_activity >= self._rotate_after
        ):
            try:
                self._sending(socket, session.end_current)
            except Exception as exc:
                raise TTSError(f"Cartesia request failed: {exc}") from exc

    def _check_stall(self, session: _Session) -> None:
        """An ended context with no `done` and nothing from the server for
        `timeout` is a stalled request, not a pause: fail instead of waiting
        forever (the socket is then dropped)."""
        with session.changed:
            pending = not session.ended <= session.done
            silent = time.monotonic() - session.last_message
        if pending and silent > self._timeout:
            session.stalled = True
            raise TTSError(
                f"Cartesia request failed: no answer within {self._timeout:g} s"
            )

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        if not text.strip():
            return  # nothing to say: the socket is not touched

        def _worker(stop: threading.Event) -> None:
            self._synthesize(slice_text(text, _SLICE_CHARS), callback, stop)

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
