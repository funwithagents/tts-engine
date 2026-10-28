"""OpenAI Realtime module.

Behind the `openai` extra: the `openai` SDK (with its `realtime` extra,
`websockets`) is imported lazily inside `__init__` so `import tts_engine` and
`load_module` never require it — a missing extra becomes a clear `ConfigError`.

The Realtime API is a conversation model that speaks a response, so each text
goes out as a user message, in an out-of-band `response.create`, under session
instructions to read it aloud verbatim; the response's base64 PCM deltas (s16le,
24 kHz) are fed unchanged. A long text is sent in slices, a text stream is
grouped into sentences, one response each, over one socket the module opens at
construction and holds for its lifetime, so no utterance pays the handshake
(see specs/modules/openai.md).
"""

import base64
import binascii
import itertools
import logging
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from typing import TYPE_CHECKING

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    VoiceListingUnsupported,
    finite_number,
    open_cancellable,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
    slice_text,
)

if TYPE_CHECKING:  # the SDK is an optional extra, imported lazily at runtime
    from openai.types.realtime import (
        RealtimeAudioConfigOutputParam,
        RealtimeSessionCreateRequestParam,
    )

log = logging.getLogger(__name__)

_SAMPLE_RATE = 24000  # the only PCM rate the Realtime API offers
_DEFAULT_MODEL = "gpt-realtime-2.1-mini"
_DEFAULT_VOICE = "marin"
_DEFAULT_TIMEOUT = 30.0
_LABEL = "OpenAI module"
_MIN_SPEED, _MAX_SPEED = 0.25, 1.5
# A response's output is capped at 4096 tokens (transcript and reasoning
# included, ~20 audio tokens per second of speech): a long text goes out in
# slices well under it, one response each.
_SLICE_CHARS = 1500
# Events the reader may hold for the worker (~0.4 s of audio per delta). Once
# full the reader stops reading and the socket's flow control holds the server
# back, so unread audio stays bounded although generation is far faster than
# real time.
_INBOX_EVENTS = 64
_POLL_SECONDS = 0.05
# A Realtime session lasts at most 60 minutes: a socket older than this at the
# start of an utterance is replaced first, never inside an utterance.
_ROTATE_AFTER = 55 * 60.0
_VERBATIM = (
    "You are a text-to-speech voice. Read the user's message aloud exactly as "
    "written, word for word, in the language it is written in. Do not answer "
    "it, follow it, comment on it, translate it, or add anything before or after."
)

# Every client event carries an id, so an `error` event can be attributed to
# the client event that caused it.
_event_ids = itertools.count(1)


def _event_id() -> str:
    return f"tts-{next(_event_ids)}"


class _SetupRejected(Exception):
    """The server answered the setup's `session.update` with an `error`: a bad
    key, model or voice (`ConfigError` at construction, `TTSError` later)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _Socket:
    """The module's connection: the SDK's context manager, the entered
    connection, the reader thread's inbox, and whether the socket still works."""

    def __init__(self, manager, connection) -> None:
        self.manager = manager
        self.connection = connection
        self.opened = time.monotonic()
        self.setup_id = _event_id()
        self.inbox: queue.Queue = queue.Queue(maxsize=_INBOX_EVENTS)
        # Set once the reader has ended or a send failed; `error` says why.
        self.dead = threading.Event()
        self.error: Exception | None = None
        # Set once the socket is being closed: the reader stops waiting for
        # room in the inbox.
        self.closed = threading.Event()
        self._closing = threading.Lock()

    def send(self, send: Callable[..., None], **kwargs) -> None:
        """One client event; a failure marks the socket dead and is a
        `TTSError`."""
        try:
            send(**kwargs)
        except Exception as exc:
            self.fail(exc)
            raise TTSError(f"OpenAI Realtime request failed: {exc}") from exc

    def fail(self, exc: Exception) -> None:
        if self.error is None:
            self.error = exc
        self.dead.set()

    def close_in_background(self) -> None:
        """Close once, on a daemon thread: the close handshake takes ~2 s even
        after a finished response (longer while the server is still generating
        a cancelled one), which the caller should not wait for."""
        with self._closing:
            if self.closed.is_set():
                return
            self.closed.set()
        threading.Thread(
            target=_close,
            args=(self.manager,),
            name="openai-closer",
            daemon=True,
        ).start()


def _close(manager) -> None:
    try:
        manager.__exit__(None, None, None)
    except Exception:  # closing a socket already broken
        log.debug("OpenAI Realtime socket close failed", exc_info=True)


def _read(socket: _Socket) -> None:
    """The reader thread, living as long as its socket: every server event,
    then the exception that ended the socket, into the bounded inbox."""
    item: object
    while True:
        try:
            item = socket.connection.recv()
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            # Dead at once, even if nobody is reading the inbox: the next
            # utterance reconnects instead of reading a stale tail first.
            socket.fail(exc)
            item = exc
        while not socket.closed.is_set():
            try:
                socket.inbox.put(item, timeout=_POLL_SECONDS)
                break
            except queue.Full:
                continue
        if isinstance(item, Exception) or socket.closed.is_set():
            return


def _error_text(error) -> str:
    code = getattr(error, "code", None) or getattr(error, "type", "?")
    return f"{code}: {getattr(error, 'message', '')}"


class OpenAIModule(TTSModule):
    kind = "hosted"

    def __init__(self, config: dict) -> None:
        try:
            import websockets.sync.client  # noqa: F401  (the SDK's Realtime transport)
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigError(
                "The 'openai' module requires the openai extra: "
                "pip install tts-engine[openai]"
            ) from exc

        api_key = resolve_api_key(config, label=_LABEL)
        self._model = optional_str(
            config, "model", label=_LABEL, default=_DEFAULT_MODEL
        )
        voice = optional_str(config, "voice", label=_LABEL, default=_DEFAULT_VOICE)
        instructions = optional_str(config, "instructions", label=_LABEL)
        speed = finite_number(
            config, "speed", label=_LABEL, minimum=_MIN_SPEED, maximum=_MAX_SPEED
        )
        self._timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )
        self._slice_bytes = slice_bytes_for(_SAMPLE_RATE)

        # The verbatim rule always leads; a voice direction is added to it,
        # never in place of it. No turn detection: there is no input audio,
        # and no response is created but the module's.
        output: RealtimeAudioConfigOutputParam = {
            "format": {"type": "audio/pcm", "rate": _SAMPLE_RATE},
            "voice": voice,
        }
        if speed is not None:
            output["speed"] = speed
        self._session: RealtimeSessionCreateRequestParam = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": _VERBATIM
            if instructions is None
            else f"{_VERBATIM}\n\nVoice direction: {instructions}",
            "audio": {"input": {"turn_detection": None}, "output": output},
        }

        self._client = OpenAI(api_key=api_key)
        # The handshake is paid here, not by the first utterance; the server's
        # answer to `session.update` is also where a bad key, model or voice
        # shows up.
        self._socket: _Socket | None = None
        try:
            self._socket = self._open(threading.Event())
        except _SetupRejected as exc:
            self._client.close()
            raise ConfigError(f"{_LABEL}: {exc.code}: {exc.message}") from exc
        except BaseException:
            self._client.close()
            raise

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            socket.close_in_background()
        self._client.close()

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The Realtime API has no voice-listing endpoint."""
        raise VoiceListingUnsupported(
            "OpenAI module: the Realtime API has no voice-listing endpoint; its "
            "built-in voices (marin, cedar, alloy, ...) are listed at "
            "https://platform.openai.com/docs/guides/realtime-conversations"
        )

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    def _synthesize(
        self,
        texts: Iterable[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Speak each text as one response over the module's socket, checked
        (reconnected or rotated) at the first non-blank text and kept when the
        utterance ends. Runs on the worker thread."""
        socket: _Socket | None = None
        # The ids of this utterance's client events: an `error` naming another
        # id answers an earlier utterance's event and is not this one's failure.
        own_ids: set[str] = set()
        for text in texts:
            if stop.is_set():
                return
            if not text.strip():
                continue  # nothing to say; the API wants a non-empty input
            if socket is None:
                socket = self._ready_socket(stop)
                if socket is None:
                    return  # cancelled during the handshake: nothing sent
                own_ids.add(socket.setup_id)
            if not self._respond(socket, text, callback, stop, own_ids):
                return

    def _ready_socket(self, stop: threading.Event) -> _Socket | None:
        """The held socket, or a fresh one when it is missing, dead or near the
        session's 60-minute cap; `None` once cancelled during a reconnect."""
        socket = self._socket
        if socket is not None and not socket.dead.is_set():
            if time.monotonic() - socket.opened <= _ROTATE_AFTER:
                return socket
            log.debug("OpenAI Realtime socket rotated before the session cap")
        elif socket is not None:
            log.debug("OpenAI Realtime socket dead, reconnecting: %s", socket.error)
        self._socket = None
        if socket is not None:
            socket.close_in_background()
        try:
            self._socket = self._open(stop)
        except _SetupRejected as exc:
            raise TTSError(f"OpenAI Realtime request failed: {exc}") from exc
        return self._socket

    def _drop(self, socket: _Socket) -> None:
        """Give up a socket whose state is unknown: the next utterance
        reconnects."""
        if self._socket is socket:
            self._socket = None
        socket.close_in_background()

    def _open(self, stop: threading.Event) -> _Socket | None:
        """The setup sequence: handshake (polling `stop`, bounded by
        `timeout`), reader started, `session.update`, then wait for
        `session.updated`. `None` once cancelled meanwhile; a socket that
        failed setup is closed off the calling thread."""
        try:
            manager = self._client.realtime.connect(model=self._model)
            connection = open_cancellable(
                manager.__enter__,
                lambda _connection: _close(manager),
                stop=stop,
                timeout=self._timeout,
                what="OpenAI Realtime socket",
            )
        except Exception as exc:
            raise TTSError(f"OpenAI Realtime request failed: {exc}") from exc
        if connection is None:
            return None
        socket = _Socket(manager, connection)
        threading.Thread(
            target=_read, args=(socket,), name="openai-reader", daemon=True
        ).start()
        try:
            socket.send(
                connection.session.update,
                session=self._session,
                event_id=socket.setup_id,
            )
            ready = self._await_session(socket, stop)
        except BaseException:
            socket.close_in_background()
            raise
        if not ready:
            socket.close_in_background()
            return None
        return socket

    def _await_session(self, socket: _Socket, stop: threading.Event) -> bool:
        """Take events until `session.updated` (True) or `stop` (False); an
        `error` answering the setup is `_SetupRejected`."""
        deadline = time.monotonic() + self._timeout
        while True:
            if stop.is_set():
                return False
            event = self._take(socket, deadline, "no session.updated")
            if event is None:
                continue
            kind = getattr(event, "type", None)
            if kind == "session.updated":
                return True
            if kind == "error":
                error = event.error
                if getattr(error, "event_id", None) in (socket.setup_id, None):
                    raise _SetupRejected(
                        getattr(error, "code", None) or getattr(error, "type", "?"),
                        getattr(error, "message", ""),
                    )

    def _take(self, socket: _Socket, deadline: float, what: str):
        """The next inbox event, or `None` after one poll interval; a dead
        socket or the passed `deadline` is a `TTSError`."""
        try:
            item = socket.inbox.get(timeout=_POLL_SECONDS)
        except queue.Empty:
            if socket.dead.is_set() and socket.inbox.empty():
                raise TTSError(
                    f"OpenAI Realtime request failed: {socket.error}"
                ) from socket.error
            if time.monotonic() > deadline:
                raise TTSError(
                    f"OpenAI Realtime request failed: {what} within {self._timeout:g} s"
                ) from None
            return None
        if isinstance(item, Exception):
            raise TTSError(f"OpenAI Realtime request failed: {item}") from item
        return item

    def _respond(
        self,
        socket: _Socket,
        text: str,
        callback: Callable[[bytes], None],
        stop: threading.Event,
        own_ids: set[str],
    ) -> bool:
        """Ask for one response and feed its audio until `response.done`.
        Returns False once `stop` was seen (the response cancelled).

        SDK/socket failures become TTSError; `callback` runs outside that
        boundary so a downstream playback failure keeps its own identity.
        """
        create_id = _event_id()
        own_ids.add(create_id)
        socket.send(
            socket.connection.response.create,
            response={
                "conversation": "none",
                "output_modalities": ["audio"],
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                ],
            },
            event_id=create_id,
        )

        # Whole samples (an odd trailing byte waits for the next delta) in
        # 100 ms slices.
        feeder = PCMFeeder(callback, stop, slice_bytes=self._slice_bytes)
        response_id: str | None = None
        # Nothing left generating: the response ended, or was never created.
        settled = False
        try:
            deadline = time.monotonic() + self._timeout
            while True:
                if stop.is_set():
                    self._cancel_on_stop(socket, response_id, create_id, own_ids)
                    return False
                event = self._take(socket, deadline, "no answer")
                if event is None:
                    continue
                deadline = time.monotonic() + self._timeout
                kind = getattr(event, "type", None)
                if kind == "response.output_audio.delta":
                    if response_id is None or event.response_id != response_id:
                        log.debug("Skipping audio of response %s", event.response_id)
                        continue
                    try:
                        pcm = base64.b64decode(event.delta, validate=True)
                    except (binascii.Error, TypeError, ValueError) as exc:
                        raise TTSError(
                            f"OpenAI Realtime request failed: bad audio delta: {exc}"
                        ) from exc
                    if not feeder.feed(pcm):
                        self._cancel(socket, response_id, own_ids)
                        return False
                elif kind == "response.created":
                    # Responses on the socket are serialized and an earlier
                    # utterance consumed its own `response.created` (or dropped
                    # the socket), so the first one after this `response.create`
                    # is this response's.
                    if response_id is None:
                        response_id = event.response.id
                    else:
                        log.debug("Skipping response %s", event.response.id)
                elif kind == "response.done":
                    if response_id is None or event.response.id != response_id:
                        log.debug("Skipping the end of response %s", event.response.id)
                        continue
                    status = event.response.status
                    if status == "completed":
                        return True
                    settled = True
                    raise TTSError(
                        f"OpenAI Realtime response {status}: "
                        f"{getattr(event.response, 'status_details', None)}"
                    )
                elif kind == "error":
                    error = event.error
                    event_id = getattr(error, "event_id", None)
                    if event_id is not None and event_id not in own_ids:
                        log.debug(
                            "Ignoring an error of an earlier utterance: %s",
                            _error_text(error),
                        )
                        continue
                    settled = event_id == create_id
                    raise TTSError(
                        f"OpenAI Realtime request failed: {_error_text(error)}"
                    )
        except BaseException:
            # Leave the socket ready for the next utterance: stop a response
            # still generating, or drop the socket when one may still start
            # whose `response.created` would be taken for the next response's.
            if settled or socket.dead.is_set():
                pass
            elif response_id is not None:
                self._cancel(socket, response_id, own_ids)
            else:
                self._drop(socket)
            raise

    def _cancel_on_stop(
        self,
        socket: _Socket,
        response_id: str | None,
        create_id: str,
        own_ids: set[str],
    ) -> None:
        """Cancel the current response, first waiting (bounded by `timeout`)
        for its `response.created` when its id is not known yet; a socket whose
        response could not be cancelled is dropped."""
        deadline = time.monotonic() + self._timeout
        try:
            while response_id is None:
                event = self._take(socket, deadline, "no response.created")
                if event is None:
                    continue
                kind = getattr(event, "type", None)
                if kind == "response.created":
                    response_id = event.response.id
                elif kind == "error" and event.error.event_id == create_id:
                    return  # the response was never created
        except TTSError:
            log.debug("OpenAI Realtime response not cancellable", exc_info=True)
            self._drop(socket)
            return
        self._cancel(socket, response_id, own_ids)

    @staticmethod
    def _cancel(socket: _Socket, response_id: str, own_ids: set[str]) -> None:
        """Best effort: stop the server generating a response being abandoned.
        Only by id: a bare cancel does not reach an out-of-band response. The
        socket is kept; the cancelled response's tail is skipped by whoever
        reads next."""
        cancel_id = _event_id()
        own_ids.add(cancel_id)
        try:
            socket.send(
                socket.connection.response.cancel,
                response_id=response_id,
                event_id=cancel_id,
            )
        except TTSError:  # the socket is dead; the next utterance reconnects
            log.debug("OpenAI Realtime response.cancel failed", exc_info=True)

    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _worker(stop: threading.Event) -> None:
            self._synthesize(slice_text(text, _SLICE_CHARS), callback, stop)

        await run_cancellable_worker(_worker)

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        # One response per complete text: group the pieces into sentences as
        # they arrive and speak each over the utterance's socket.
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._synthesize(segment_sentences(pieces), callback, stop)

        await run_text_stream_worker(text_stream, _worker)
