"""Deepgram streaming TTS module: Aura-2 on speak v1, Flux TTS on speak v2.

Behind the `deepgram` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra
becomes a clear `ConfigError`.

The model picks the API: a `flux-` model runs on the speak v2 WebSocket
(`speak.v2.connect`), anything else on speak v1. The two sockets share one
shape and differ in three places: the text message (`Speak` through
`send_speak` on v2, `send_text` on v1), the answer that marks a flushed
segment fully generated (v2's `SpeechMetadata`, v1's `Flushed` — v2's own
`Flushed` arrives before the audio), and v2's `Error` event.

Both input shapes run over that WebSocket, which takes text incrementally: a complete text is sent in small slices, a
text stream is forwarded as it arrives, so this is a true stream-to-stream
pass. Three threads share the socket: a sender (text, `Flush`, `Close`), a
reader (the SDK's blocking message iterator, into a queue) and the worker,
which polls that queue so it can honor `stop` while the server is silent. The
sender starts a segment only once the previous one is fully generated, which
bounds the text the server holds unspoken under its limit, and admits a text message only while the audio
read but not yet fed stays under `_MAX_UNREAD_SECONDS`, which bounds how far
the provider runs ahead of playback (see specs/modules/deepgram.md,
"Pending-text backpressure" and "Bounded unread audio").
"""

import logging
import queue
import re
import threading
import time
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

_ENDS_SENTENCE = re.compile(r"(?:[.!?…][\"'”’)\]]*|\n)\s*$")

_END = object()  # reader sentinel: the server ended the message iterator


class _Session:
    """State shared by the three threads of one utterance."""

    def __init__(self) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.flushes_sent = 0
        self.flushes_answered = 0
        self.close_sent = False
        self.unread = 0  # audio bytes in the inbox: read, not yet fed
        self.last_message = time.monotonic()  # when the server last spoke
        self.changed = threading.Condition()  # guards the counters above
        self.sender_errors: list[Exception] = []

    def done(self) -> bool:
        """Every flushed segment finished after the final Flush was sent: all
        audio is in."""
        with self.changed:
            return self.close_sent and self.flushes_answered >= self.flushes_sent


class DeepgramModule(TTSModule):
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
        # The key is always passed explicitly: the SDK's own DEEPGRAM_API_KEY
        # fallback is never relied on, so this module's key rules are the only
        # ones in force.
        self._client = DeepgramClient(api_key=api_key)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _read(self, socket, session: _Session) -> None:
        """Reader thread: move the SDK's messages into the inbox, counting the
        audio it holds and the segments the server has finished, so the
        sender's gates open without the worker."""
        try:
            for message in socket:
                with session.changed:
                    session.last_message = time.monotonic()
                    if isinstance(message, bytes):
                        session.unread += len(message)
                    elif getattr(message, "type", None) == self._segment_done:
                        session.flushes_answered += 1
                    session.changed.notify_all()
                session.inbox.put(message)
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            session.inbox.put(exc)
        else:
            session.inbox.put(_END)

    def _send(
        self,
        socket,
        pieces: Iterator[str],
        session: _Session,
        stop: threading.Event,
    ) -> None:
        """Sender thread: forward `pieces` grouped at word boundaries, cut into
        flushed segments, a segment started only once the previous Flush is
        answered and each text message admitted only while the unread audio
        is under its bound, then the final Flush and Close. Sends nothing
        more once `stop` is set."""
        since_flush = 0

        def flush() -> None:
            nonlocal since_flush
            with session.changed:
                socket.send_flush()
                session.flushes_sent += 1
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
            if not stop.is_set():
                message = self._speak_type(text=text)
                if self._flux:
                    socket.send_speak(message)
                else:
                    socket.send_text(message)

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
                return  # aborted: the worker closes the socket
            send(buffer)
            if since_flush:
                flush()  # the remainder; an empty stream sends none
            if stop.is_set():
                return
            with session.changed:
                socket.send_close()
                session.close_sent = True
        except Exception as exc:  # noqa: BLE001  (surfaced by the worker)
            if not stop.is_set():
                session.sender_errors.append(exc)

    def _synthesize(
        self,
        pieces: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Run one utterance over one socket on the worker thread: open the
        socket (polling `stop`, bounded by `timeout`), start the sender and
        reader, then feed audio from the inbox until every Flush is answered,
        the server closes after our Close, `stop`, or a failure.

        SDK/socket failures become TTSError; `callback` runs outside that
        boundary so a downstream playback failure keeps its own identity.
        """
        try:
            api = self._client.speak.v2 if self._flux else self._client.speak.v1
            connection = api.connect(**self._connect_kwargs)
            socket = open_cancellable(
                connection.__enter__,
                lambda _socket: self._close(connection),
                stop=stop,
                timeout=self._timeout,
                what="Deepgram socket",
            )
        except Exception as exc:
            raise TTSError(f"Deepgram request failed: {exc}") from exc
        if socket is None:
            return  # cancelled during the handshake: nothing was sent

        session = _Session()
        try:
            threading.Thread(
                target=self._read,
                args=(socket, session),
                name="deepgram-reader",
                daemon=True,
            ).start()
            # Daemon: a sender blocked on text after the worker has left is
            # released when `stop` or end-of-stream reaches `pieces`; its late
            # send on the closed socket is then ignored.
            threading.Thread(
                target=self._send,
                args=(socket, pieces, session, stop),
                name="deepgram-sender",
                daemon=True,
            ).start()
            self._receive(session, callback, stop)
        finally:
            aborted = not session.done()
            # Release a sender still blocked on text, on a Flush or on the gate.
            stop.set()
            if aborted:
                # The server answers the close handshake only once it has
                # finished generating (seconds for a long text). Callbacks
                # have stopped, so close off the worker thread and return now.
                threading.Thread(
                    target=self._close,
                    args=(connection,),
                    name="deepgram-closer",
                    daemon=True,
                ).start()
            else:
                self._close(connection)

    @staticmethod
    def _close(connection) -> None:
        try:
            connection.__exit__(None, None, None)
        except Exception:  # closing a socket already broken
            log.debug("Deepgram socket close failed", exc_info=True)

    def _receive(
        self,
        session: _Session,
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        # Whole samples (an odd trailing byte waits for the next message) in
        # 100 ms slices.
        feeder = PCMFeeder(callback, stop, slice_bytes=self._slice_bytes)
        while not stop.is_set():
            if session.sender_errors:
                exc = session.sender_errors[0]
                raise TTSError(f"Deepgram request failed: {exc}") from exc
            if session.done() and session.inbox.empty():
                return
            try:
                message = session.inbox.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                self._check_stall(session)
                continue
            if message is _END:
                if session.close_sent:
                    return  # the server closed after our Close: all audio in
                raise TTSError(
                    "Deepgram request failed: the server closed the connection"
                )
            if isinstance(message, Exception):
                if session.done():
                    return  # dropped after the last Flushed: nothing was lost
                raise TTSError(f"Deepgram request failed: {message}") from message
            if isinstance(message, bytes):
                with session.changed:
                    session.unread -= len(message)
                    session.changed.notify_all()
                if not feeder.feed(message):
                    return
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
                    getattr(message, "warn_code", getattr(message, "code", "?")),
                    getattr(message, "warn_msg", getattr(message, "description", "")),
                )

    def _check_stall(self, session: _Session) -> None:
        """A Flush unanswered with nothing from the server for `timeout` is a
        stalled request, not a pause: fail instead of waiting forever."""
        with session.changed:
            pending = session.flushes_answered < session.flushes_sent
            silent = time.monotonic() - session.last_message
        if pending and silent > self._timeout:
            raise TTSError(
                f"Deepgram request failed: no answer within {self._timeout:g} s"
            )

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
        def _worker(pieces: Iterator[str], stop: threading.Event) -> None:
            self._synthesize(pieces, callback, stop)

        await run_text_stream_worker(text_stream, _worker)
