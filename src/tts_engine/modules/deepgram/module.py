"""Deepgram streaming TTS module: Aura-2 on speak v1, Flux TTS on speak v2.

Behind the `deepgram` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

The model picks the API: a `flux-` model runs on the speak v2 WebSocket
(`speak.v2.connect`), anything else on speak v1. The two sockets share one
shape and differ in a few places: the text message (`Speak` through
`send_speak` on v2, `send_text` on v1), the answer that marks a flushed
segment fully generated (v2's `SpeechMetadata`, v1's `Flushed` — v2's own
`Flushed` arrives before the audio), the cancel (`Interrupt` on v2, `Clear` on
v1), and v2's `Error` event.

The module opens one socket at construction and holds it for its lifetime:
every utterance runs over it, ends on the answer to its last `Flush` (no
`Close`), and a cancelled one is stopped with `Clear` / `Interrupt` instead of
closing the socket. A reader thread per socket moves the server's messages to
the utterance attached to it; per utterance, a sender forwards text and the
worker polls its inbox so it can honor `stop` while the server is silent. The
sender starts a segment only once the previous one is fully generated, which
bounds the text the server holds unspoken under its limit, and admits a text
message only while the audio read but not yet fed stays under
`_MAX_UNREAD_SECONDS`, which bounds how far the provider runs ahead of
playback (see specs/modules/deepgram.md).
"""

import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator

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

_DEFAULT_MODEL = "aura-2-thalia-en"
_SAMPLE_RATES = (8000, 16000, 24000, 32000, 48000)
# A `flux-` model selects speak v2 (Flux TTS); anything else speak v1 (Aura).
_FLUX_PREFIX = "flux-"
_SPEED_RANGE = (0.7, 1.5)
# Flux accepts 0.5–1.5 in steps of 0.05 (anything else is HTTP 400).
_FLUX_SPEED_RANGE = (0.5, 1.5)
_FLUX_SPEED_STEP = 0.05
_EXPRESSIVITY_RANGE = (-2, 2)
_DEFAULT_TIMEOUT = 30.0
_LABEL = "Deepgram module"
# A segment (the text between two Flushes) is flushed at the first sentence end
# past _SEGMENT_SOFT characters, and at a word boundary at _SEGMENT_HARD at the
# latest. The next segment starts only once the Flush is answered, so the
# server never holds more than one segment unspoken — under its
# ~2000-character pending-text limit — and the audio read ahead of playback
# is bounded by one segment plus the lead below.
_SEGMENT_SOFT = 600
_SEGMENT_HARD = 900
# The sender admits a text message only while the audio read and not yet fed
# is under this much: the provider runs at most this far ahead of playback,
# plus the audio of the text already admitted.
_MAX_UNREAD_SECONDS = 10.0
# A complete text is sent in slices of at most this many characters (a few
# seconds of speech), so the admission gate has a grain to work on.
_SLICE_CHARS = 50
# How often the worker (and a sender waiting on a Flush) wakes to poll `stop`.
_POLL_SECONDS = 0.1
# Flux caps a session at one hour (NET-0003): a socket older than this at the
# start of an utterance is replaced first, never inside an utterance. The same
# age applies on v1, where no cap is documented.
_ROTATE_AFTER = 55 * 60.0
# Inside the 600 s verified alive idle on both v1 and v2; Deepgram documents no
# idle limit (Flux's 60 s inactivity window is reset by websockets' default
# 20 s pings). A socket idle longer is replaced before its utterance, so the
# module never relies on an unmeasured server idle cutoff.
_IDLE_REPLACE = 9 * 60.0
# `time.monotonic()` stops while the host sleeps (mach_absolute_time() on
# macOS, CLOCK_MONOTONIC on Linux); the wall clock does not. A wall-clock idle
# time this far ahead of the monotonic one (one websockets ping interval) means
# the host slept through a keepalive, and the socket may be half-open.
_SLEPT = 20.0
# v1 accepts at most 20 Flushes per socket in any 60 s (then EXCESSIVE_FLUSH,
# the Flush ignored). A v1 socket with this many sent in the window is
# replaced before its utterance, leaving the utterance room for its own.
_FLUSH_WINDOW = 60.0
_FLUSH_BUDGET = 16
# A socket also refuses text past about 2400 characters within a recent window
# (1008 DATA-0001, "too many characters submitted within a recent time
# window"; measured per socket, cleared by a new one). At playback pace (about
# 1000 characters a minute) it is never reached; a sink faster than real time
# and a fast model reach it across utterances. A socket with this many sent in
# the last minute is replaced before its utterance, leaving the utterance room
# for more than one segment.
_CHAR_WINDOW = 60.0
_CHAR_BUDGET = 1200

_ENDS_SENTENCE = re.compile(r"(?:[.!?…][\"'”’)\]]*|\n)\s*$")
# v2 warnings meaning an Interrupt was ignored, with no SpeechInterrupted coming
# (nothing is generating then).
_INTERRUPT_WARNINGS = frozenset(
    {"INTERRUPT_IN_PROGRESS", "NO_AUDIO_GENERATED", "INVALID_INTERRUPT_OFFSET"}
)
# Handshake statuses that are the config's fault: a parameter refused (400), a
# bad key (401), an unknown model or voice (403).
_REJECTED_STATUSES = (400, 401, 403)

_END = object()  # reader sentinel: the server ended the message iterator


class _SocketUnknown(TTSError):
    """A failure after which the socket's state is unknown (a stall, a refused
    mid-utterance Flush): it is dropped, and the next utterance reconnects."""


def _warning_code(message) -> str:
    return getattr(message, "warn_code", None) or getattr(message, "code", "?")


def _rejected_status(exc: BaseException) -> int | None:
    """The HTTP status of a handshake `websockets` rejected (`InvalidStatus`),
    read by duck typing so nothing of `websockets` is imported here."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _close(manager) -> None:
    try:
        manager.__exit__(None, None, None)
    except Exception:  # closing a socket already broken
        log.debug("Deepgram socket close failed", exc_info=True)


class _Session:
    """State of one utterance, shared by its sender and worker and by the
    socket's reader while the utterance is attached."""

    def __init__(self) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.flushes_sent = 0
        self.flushes_answered = 0
        self.final_sent = False  # the last Flush sent (or an empty stream ended)
        self.close_sent = False  # the EXCESSIVE_FLUSH fallback
        self.flush_refused = False  # an EXCESSIVE_FLUSH arrived
        self.unread = 0  # audio bytes in the inbox: read, not yet fed
        self.last_message = time.monotonic()  # when the server last spoke
        self.changed = threading.Condition()  # guards the fields above
        self.sender_errors: list[Exception] = []

    def done(self) -> bool:
        """Every flushed segment finished after the final Flush was sent: all
        audio is in."""
        with self.changed:
            return self.final_sent and self.flushes_answered >= self.flushes_sent


class _Socket:
    """The module's connection: the SDK manager and client, whether it still
    works, the utterance attached to it, and the state that outlives an
    utterance (a cancel settling, the flush budget, the idle clocks)."""

    def __init__(self, manager, client) -> None:
        self.manager = manager
        self.client = client
        self.opened = time.monotonic()
        self.last_mono = self.opened  # last message sent or received, ...
        self.last_wall = time.time()  # ... on both clocks (see _SLEPT)
        self.dead = threading.Event()
        self.error: BaseException | None = None
        self.closed = threading.Event()
        self._closing = threading.Lock()
        # Every send goes through this lock, and the sender checks `stop`
        # inside it: no text of an aborted utterance can follow its cancel.
        self.send_lock = threading.Lock()
        # Guards `session` and `settling` transitions against the reader.
        self.lock = threading.Lock()
        self.session: _Session | None = None
        # Set from an aborted utterance's Clear / Interrupt until its answer:
        # the reader drops everything meanwhile.
        self.settling = threading.Event()
        self.flush_times: deque[float] = deque()
        self.char_log: deque[tuple[float, int]] = deque()  # (sent at, characters)
        self.exhausted = False  # an EXCESSIVE_FLUSH seen: replace before reuse

    def touch(self) -> None:
        self.last_mono = time.monotonic()
        self.last_wall = time.time()

    def send(self, fn: Callable, *args, stop: threading.Event | None = None) -> bool:
        """One client message, under the send lock. False (nothing sent) once
        `stop` is set; a failure marks the socket dead and propagates."""
        with self.send_lock:
            if stop is not None and stop.is_set():
                return False
            try:
                fn(*args)
            except Exception as exc:
                self.fail(exc)
                raise
        self.touch()
        return True

    def fail(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = exc
        self.dead.set()

    def flushes_in_window(self, now: float) -> int:
        while self.flush_times and now - self.flush_times[0] > _FLUSH_WINDOW:
            self.flush_times.popleft()
        return len(self.flush_times)

    def chars_in_window(self, now: float) -> int:
        while self.char_log and now - self.char_log[0][0] > _CHAR_WINDOW:
            self.char_log.popleft()
        return sum(n for _, n in self.char_log)

    def close_in_background(self) -> None:
        """Close once, on a daemon thread: the caller never waits for the
        close handshake."""
        with self._closing:
            if self.closed.is_set():
                return
            self.closed.set()
        threading.Thread(
            target=_close, args=(self.manager,), name="deepgram-closer", daemon=True
        ).start()


class DeepgramModule(TTSModule):
    kind = "hosted"

    def __init__(self, config: dict) -> None:
        # Read first (unvalidated) only to pick the SDK type to import; it is
        # validated below with the other fields.
        model_name = config.get("model", _DEFAULT_MODEL)
        model_name = model_name if isinstance(model_name, str) else ""
        try:
            from deepgram import DeepgramClient

            if model_name.startswith(_FLUX_PREFIX):
                from deepgram.speak.v2.types import SpeakV2Speak as speak_type
            else:
                from deepgram.speak.v1.types import SpeakV1Text as speak_type
        except ImportError as exc:
            raise ConfigError(
                "The 'deepgram' module requires the deepgram extra: "
                "pip install tts-engine[deepgram]"
            ) from exc

        api_key = resolve_api_key(config, label=_LABEL)

        model = optional_str(config, "model", label=_LABEL, default=_DEFAULT_MODEL)
        flux = model.startswith(_FLUX_PREFIX)

        sample_rate = config.get("sample_rate", 24000)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate not in _SAMPLE_RATES
        ):
            raise ConfigError(
                "Deepgram module 'sample_rate' must be one of "
                + ", ".join(str(r) for r in _SAMPLE_RATES)
            )

        connect_kwargs: dict = {
            "model": model,
            "encoding": "linear16",
            "sample_rate": sample_rate,
        }
        low, high = _FLUX_SPEED_RANGE if flux else _SPEED_RANGE
        speed = finite_number(config, "speed", label=_LABEL, minimum=low, maximum=high)
        if speed is not None:
            steps = speed / _FLUX_SPEED_STEP
            if flux and abs(steps - round(steps)) > 1e-6:
                raise ConfigError(
                    "Deepgram module 'speed' must be a multiple of 0.05 "
                    "with a Flux model"
                )
            connect_kwargs["speed"] = speed
        expressivity = config.get("expressivity")
        if expressivity is not None:
            if not flux:
                raise ConfigError(
                    "Deepgram module 'expressivity' requires a Flux model "
                    f"('{_FLUX_PREFIX}…')"
                )
            if (
                isinstance(expressivity, bool)
                or not isinstance(expressivity, int)
                or not _EXPRESSIVITY_RANGE[0] <= expressivity <= _EXPRESSIVITY_RANGE[1]
            ):
                raise ConfigError(
                    "Deepgram module 'expressivity' must be an integer from -2 to 2"
                )
            connect_kwargs["expressivity"] = expressivity
        mip_opt_out = config.get("mip_opt_out")
        if mip_opt_out is not None:
            if not isinstance(mip_opt_out, bool):
                raise ConfigError("Deepgram module 'mip_opt_out' must be a boolean")
            connect_kwargs["mip_opt_out"] = mip_opt_out

        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )

        self._sample_rate: int = sample_rate
        self._connect_kwargs = connect_kwargs
        self._timeout = float(timeout)
        self._max_unread = int(_MAX_UNREAD_SECONDS * 2 * sample_rate)
        self._slice_bytes = slice_bytes_for(sample_rate)
        self._flux = flux
        self._speak_type = speak_type
        # The answer that marks a flushed segment fully generated: v2 sends
        # `Flushed` when it starts the turn and `SpeechMetadata` after its
        # last audio; v1's `Flushed` comes after the audio.
        self._segment_done = "SpeechMetadata" if flux else "Flushed"
        # The answer to the cancel message (`Interrupt` on v2, `Clear` on v1).
        self._cancel_done = "SpeechInterrupted" if flux else "Cleared"
        # The key is always passed explicitly: the SDK's own DEEPGRAM_API_KEY
        # fallback is never relied on, so this module's key rules are the only
        # ones in force.
        self._client = DeepgramClient(api_key=api_key)
        # The handshake is paid here, not by the first utterance; it is also
        # where the API rejects a bad key, model or parameter.
        self._socket: _Socket | None = None
        try:
            self._socket = self._open(threading.Event())
        except Exception as exc:
            status = _rejected_status(exc)
            if status in _REJECTED_STATUSES:
                raise ConfigError(
                    f"{_LABEL}: handshake rejected: HTTP {status}"
                ) from exc
            raise TTSError(f"Deepgram request failed: {exc}") from exc

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            socket.close_in_background()

    # -- the socket ---------------------------------------------------------

    def _open(self, stop: threading.Event) -> _Socket | None:
        """Handshake (polling `stop`, bounded by `timeout`) and start the
        socket's reader. `None` once cancelled meanwhile; failures propagate
        unwrapped."""
        api = self._client.speak.v2 if self._flux else self._client.speak.v1
        manager = api.connect(**self._connect_kwargs)
        client = open_cancellable(
            manager.__enter__,
            lambda _client: _close(manager),
            stop=stop,
            timeout=self._timeout,
            what="Deepgram socket",
        )
        if client is None:
            return None
        socket = _Socket(manager, client)
        threading.Thread(
            target=self._read, args=(socket,), name="deepgram-reader", daemon=True
        ).start()
        return socket

    def _drop(self, socket: _Socket) -> None:
        """Give up a socket whose state is unknown: the next utterance
        reconnects."""
        if self._socket is socket:
            self._socket = None
        socket.close_in_background()

    def _ready_socket(self, stop: threading.Event) -> _Socket | None:
        """The held socket, once a cancel settling on it has been answered;
        or a fresh one when it is missing, dead, old, idle, slept through, or
        out of flush budget. `None` once cancelled meanwhile."""
        socket = self._socket
        if socket is not None and socket.settling.is_set():
            deadline = time.monotonic() + self._timeout
            while socket.settling.is_set() and not socket.dead.is_set():
                if stop.is_set():
                    return None
                if time.monotonic() > deadline:
                    log.debug("Deepgram cancel unanswered: dropping the socket")
                    self._drop(socket)
                    break
                time.sleep(_POLL_SECONDS)
            socket = self._socket

        reason = self._replace_reason(socket)
        if reason is None:
            return socket
        log.debug("Deepgram socket replaced: %s", reason)
        self._socket = None
        if socket is not None:
            socket.close_in_background()
        try:
            self._socket = self._open(stop)
        except Exception as exc:
            raise TTSError(f"Deepgram request failed: {exc}") from exc
        return self._socket

    def _replace_reason(self, socket: _Socket | None) -> str | None:
        if socket is None:
            return "none held"
        if socket.dead.is_set():
            return f"dead ({socket.error})"
        if socket.exhausted:
            return "flush rate limit reached"
        now = time.monotonic()
        mono_idle = now - socket.last_mono
        wall_idle = time.time() - socket.last_wall
        if now - socket.opened > _ROTATE_AFTER:
            return "near the session cap"
        if max(mono_idle, wall_idle) > _IDLE_REPLACE:
            return f"idle for {max(mono_idle, wall_idle):.0f} s"
        if wall_idle - mono_idle > _SLEPT:
            return "the host slept"
        if not self._flux and socket.flushes_in_window(now) >= _FLUSH_BUDGET:
            return "flush budget spent"
        if socket.chars_in_window(now) >= _CHAR_BUDGET:
            return "character budget spent"
        return None

    def _read(self, socket: _Socket) -> None:
        """The reader thread, living as long as its socket: route each server
        message to the attached utterance, then mark the socket dead."""
        end: object = _END
        try:
            for message in socket.client:
                socket.touch()
                self._route(socket, message)
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            socket.fail(exc)
            end = exc
        else:
            socket.fail(ConnectionError("the server closed the connection"))
        with socket.lock:
            session = socket.session
        if session is not None:
            session.inbox.put(end)

    def _route(self, socket: _Socket, message) -> None:
        kind = None if isinstance(message, bytes) else getattr(message, "type", None)
        code = _warning_code(message) if kind == "Warning" else None
        if code == "EXCESSIVE_FLUSH":
            socket.exhausted = True
        with socket.lock:
            if socket.settling.is_set():
                # Everything before the cancel's answer belongs to the
                # aborted utterance.
                if kind == self._cancel_done or (
                    self._flux and code in _INTERRUPT_WARNINGS
                ):
                    socket.settling.clear()
                log.debug("Dropping %s of a cancelled utterance", kind or "audio")
                return
            session = socket.session
        if session is None:
            if kind == "Warning":
                log.warning("Deepgram warning %s while idle", code)
            else:
                log.debug("Deepgram message while idle: %s", kind or "audio")
            return
        with session.changed:
            session.last_message = time.monotonic()
            if kind is None:
                session.unread += len(message)
            elif kind == self._segment_done:
                session.flushes_answered += 1
            elif code == "EXCESSIVE_FLUSH":
                session.flush_refused = True
            session.changed.notify_all()
        session.inbox.put(message)

    # -- one utterance ------------------------------------------------------

    def _send(
        self,
        socket: _Socket,
        pieces: Iterator[str],
        session: _Session,
        stop: threading.Event,
    ) -> None:
        """Sender thread: forward `pieces` grouped at word boundaries, cut into
        flushed segments, a segment started only once the previous Flush is
        answered and each text message admitted only while the unread audio
        is under its bound, then the final Flush. Sends nothing more once
        `stop` is set."""
        since_flush = 0
        speak = socket.client.send_speak if self._flux else socket.client.send_text

        def flush(final: bool = False) -> None:
            nonlocal since_flush
            with session.changed:
                if socket.send(socket.client.send_flush, stop=stop):
                    socket.flush_times.append(time.monotonic())
                    session.flushes_sent += 1
                    session.final_sent = final
            since_flush = 0

        def admit(text: str) -> None:
            # Wait for playback to catch up before handing the server more
            # text (its audio would otherwise pile up unread), and for the
            # previous segment to be fully generated before starting a new one
            # (so the server holds at most one segment unspoken).
            with session.changed:
                while not stop.is_set() and (
                    session.unread > self._max_unread
                    or (
                        since_flush == 0
                        and session.flushes_answered < session.flushes_sent
                    )
                ):
                    session.changed.wait(_POLL_SECONDS)
            if socket.send(speak, self._speak_type(text=text), stop=stop):
                socket.char_log.append((time.monotonic(), len(text)))

        def send(text: str) -> None:
            nonlocal since_flush
            while text and not stop.is_set():
                room = _SEGMENT_HARD - since_flush
                if len(text) <= room:
                    admit(text)
                    since_flush += len(text)
                    if since_flush >= _SEGMENT_HARD or (
                        since_flush >= _SEGMENT_SOFT and _ENDS_SENTENCE.search(text)
                    ):
                        flush()
                    text = ""
                else:
                    cut = cut_at_boundary(text, room)
                    admit(text[:cut])
                    text = text[cut:]
                    flush()

        try:
            buffer = ""
            for piece in pieces:  # ends at end-of-stream or on `stop`
                buffer += piece
                words, buffer = split_at_last_space(buffer)
                send(words)
            if stop.is_set():
                return  # aborted: the worker cancels
            send(buffer)
            if since_flush:
                flush(final=True)  # the remainder
            else:
                # Nothing pending: the utterance ends on the answers already
                # due (none for an empty stream). A segment flushed at the
                # very end is the final one.
                with session.changed:
                    session.final_sent = not stop.is_set()
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            if not stop.is_set():
                session.sender_errors.append(exc)

    def _synthesize(
        self,
        pieces: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Run one utterance over the held socket on the worker thread: ready
        the socket (reconnected or replaced if needed), attach the utterance,
        start the sender, then feed audio from the inbox until every Flush is
        answered, `stop`, or a failure. An aborted utterance is cancelled with
        `Clear` / `Interrupt`, and the socket kept.

        SDK/socket failures become TTSError; `callback` runs outside that
        boundary so a downstream playback failure keeps its own identity.
        """
        socket = self._ready_socket(stop)
        if socket is None:
            return  # cancelled during a reconnect: nothing was sent
        session = _Session()
        with socket.lock:
            socket.session = session
        completed = drop = False
        try:
            # Daemon: a sender blocked on text after the worker has left is
            # released when `stop` or end-of-stream reaches `pieces`; it sends
            # nothing more then.
            threading.Thread(
                target=self._send,
                args=(socket, pieces, session, stop),
                name="deepgram-sender",
                daemon=True,
            ).start()
            completed = self._receive(socket, session, callback, stop)
        except _SocketUnknown:
            drop = True
            raise
        finally:
            # Release a sender still blocked on text, on a Flush or on the gate.
            stop.set()
            with socket.lock:
                if socket.session is session:
                    socket.session = None
                cancel = not (
                    completed or drop or session.close_sent or socket.dead.is_set()
                )
                if cancel:
                    socket.settling.set()
            if drop:
                self._drop(socket)
            elif cancel:
                self._cancel(socket)

    def _cancel(self, socket: _Socket) -> None:
        """Best effort: stop the server generating an aborted utterance. The
        socket is kept; its reader drops the tail until the answer."""
        fn = socket.client.send_interrupt if self._flux else socket.client.send_clear
        try:
            socket.send(fn)
        except Exception:  # the socket is dead; the next utterance reconnects
            log.debug("Deepgram cancel failed", exc_info=True)

    def _receive(
        self,
        socket: _Socket,
        session: _Session,
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> bool:
        """Feed the utterance's audio until all of it is in (True) or `stop`
        (False)."""
        # Whole samples (an odd trailing byte waits for the next message) in
        # 100 ms slices.
        feeder = PCMFeeder(callback, stop, slice_bytes=self._slice_bytes)
        while not stop.is_set():
            if session.sender_errors:
                exc = session.sender_errors[0]
                raise TTSError(f"Deepgram request failed: {exc}") from exc
            self._check_refused(socket, session)
            if session.done() and session.inbox.empty():
                return True
            try:
                message = session.inbox.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                self._check_stall(session)
                continue
            if message is _END:
                if session.close_sent:
                    return True  # the server closed after our Close: all in
                raise TTSError(
                    "Deepgram request failed: the server closed the connection"
                )
            if isinstance(message, Exception):
                if session.done() or session.close_sent:
                    return True  # dropped after the last answer: nothing lost
                raise TTSError(f"Deepgram request failed: {message}") from message
            if isinstance(message, bytes):
                with session.changed:
                    session.unread -= len(message)
                    session.changed.notify_all()
                if not feeder.feed(message):
                    return False
                continue
            kind = getattr(message, "type", None)
            if kind == "Error":  # v2 only; v1 reports errors as a close code
                raise TTSError(
                    "Deepgram request failed: "
                    f"{getattr(message, 'code', '?')} "
                    f"{getattr(message, 'description', '')}".rstrip()
                )
            if kind == "Warning":
                log.warning(
                    "Deepgram warning %s: %s",
                    _warning_code(message),
                    getattr(message, "warn_msg", getattr(message, "description", "")),
                )
        return False

    def _check_refused(self, socket: _Socket, session: _Session) -> None:
        """An EXCESSIVE_FLUSH: the refused Flush is never answered. If it was
        the final one, `Close` makes the server speak the rest and close (the
        utterance then ends on that close); a refused mid-utterance Flush
        leaves the sender stuck behind its gate."""
        with session.changed:
            if not session.flush_refused or session.close_sent:
                return
            final = session.final_sent
            if final:
                session.close_sent = True
        if not final:
            raise _SocketUnknown("Deepgram request failed: flush rate limit exceeded")
        try:
            socket.send(socket.client.send_close)
        except Exception as exc:
            raise TTSError(f"Deepgram request failed: {exc}") from exc

    def _check_stall(self, session: _Session) -> None:
        """A Flush unanswered with nothing from the server for `timeout` is a
        stalled request, not a pause: fail instead of waiting forever."""
        with session.changed:
            pending = session.flushes_answered < session.flushes_sent
            silent = time.monotonic() - session.last_message
        if pending and silent > self._timeout:
            raise _SocketUnknown(
                f"Deepgram request failed: no answer within {self._timeout:g} s"
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
