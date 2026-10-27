"""Unit tests for DeepgramModule with the deepgram SDK faked.

The fast tier never opens a socket, so `deepgram` (and the
`deepgram.speak.v1.types` path the module imports `SpeakV1Text` from) is faked
via `sys.modules`. The fake socket answers the way the live API was observed
to: audio after each `Speak`, `Flushed` per `Flush` (optionally held back to
exercise the module's gate), and a server-side close after `Close`.
"""

import asyncio
import logging
import queue
import sys
import threading
import time
import types

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions, slice_text
from tts_engine.modules.deepgram import DeepgramModule
from tts_engine.modules.deepgram import module as deepgram_module

_BASE = {"type": "deepgram", "api_key": "k"}
_AUDIO = b"\x01\x00\x02\x00"
_END = object()


class _Text:
    """Stands in for `SpeakV1Text`."""

    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeSocket:
    """The SDK's `V1SocketClient`: send methods plus a blocking message iterator."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self._outbox: queue.Queue = queue.Queue()
        self.sent: list = []  # str for Speak text, "FLUSH", "CLOSE"
        self.closed = threading.Event()
        self.flushes_delivered = 0
        self.max_unanswered_at_flush = 0

    # -- what the module sends ------------------------------------------------

    def send_text(self, message: _Text) -> None:
        self._check_send()
        self.sent.append(message.text)
        if self._backend.audio_per_text:
            self._outbox.put(self._backend.audio_per_text)

    def send_flush(self) -> None:
        self._check_send()
        unanswered = self.sent.count("FLUSH") - self.flushes_delivered
        self.max_unanswered_at_flush = max(self.max_unanswered_at_flush, unanswered)
        self.sent.append("FLUSH")
        if self._backend.hold_flushed is None:
            self._outbox.put(types.SimpleNamespace(type="Flushed"))
        else:
            self._backend.held += 1

    def send_close(self) -> None:
        self._check_send()
        self.sent.append("CLOSE")
        self._outbox.put(_END)

    def _check_send(self) -> None:
        if self._backend.raise_on_send is not None:
            raise self._backend.raise_on_send
        if self.closed.is_set():
            raise ConnectionError("socket closed")

    # -- what the server sends ------------------------------------------------

    def release_flushed(self) -> None:
        for _ in range(self._backend.held):
            self._outbox.put(types.SimpleNamespace(type="Flushed"))
        self._backend.held = 0

    def __iter__(self):
        if self._backend.script is not None:
            # Fully scripted server: these messages (raising exceptions), then
            # the server ends the stream whatever the module sent.
            for message in self._backend.script:
                if isinstance(message, Exception):
                    raise message
                yield message
            return
        for message in self._backend.preamble:
            yield message
        while True:
            if self._backend.endless_audio and self._outbox.empty():
                if self.closed.wait(0.001):
                    return
                yield _AUDIO
                continue
            message = self._outbox.get()
            if message is _END:
                return
            if isinstance(message, Exception):
                raise message
            if getattr(message, "type", None) == "Flushed":
                self.flushes_delivered += 1
            yield message

    def server_send(self, message) -> None:
        self._outbox.put(message)


class _FakeConnection:
    """What `speak.v1.connect(...)` returns: a context manager over the socket."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend

    def __enter__(self) -> _FakeSocket:
        if self._backend.raise_on_connect is not None:
            raise self._backend.raise_on_connect
        if self._backend.connect_gate is not None:
            self._backend.connecting.set()
            self._backend.connect_gate.wait(5)  # a slow handshake
        socket = _FakeSocket(self._backend)
        self._backend.sockets.append(socket)
        return socket

    def __exit__(self, *_exc) -> bool:
        socket = self._backend.sockets[-1]
        socket.closed.set()
        socket.server_send(_END)
        return False


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.connect_kwargs: list[dict] = []
        self.sockets: list[_FakeSocket] = []
        self.preamble: list = []  # messages the server sends on connect
        self.script: list | None = None
        self.audio_per_text: bytes | None = _AUDIO
        self.endless_audio = False
        self.hold_flushed: bool | None = None
        self.held = 0
        self.raise_on_connect: Exception | None = None
        self.raise_on_send: Exception | None = None
        self.connect_gate: threading.Event | None = None  # holds the handshake
        self.connecting = threading.Event()

    @property
    def socket(self) -> _FakeSocket:
        return self.sockets[-1]

    @property
    def texts(self) -> list[str]:
        return [m for m in self.socket.sent if m not in ("FLUSH", "CLOSE")]


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class V1:
        def connect(self, **kwargs):
            backend.connect_kwargs.append(kwargs)
            return _FakeConnection(backend)

    class DeepgramClient:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.speak = types.SimpleNamespace(v1=V1())

    package = types.ModuleType("deepgram")
    package.DeepgramClient = DeepgramClient  # type: ignore[attr-defined]
    speak_types = types.ModuleType("deepgram.speak.v1.types")
    speak_types.SpeakV1Text = _Text  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepgram", package)
    monkeypatch.setitem(sys.modules, "deepgram.speak", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "deepgram.speak.v1", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "deepgram.speak.v1.types", speak_types)
    # The worker and a gated sender poll on this interval: tests wait on the
    # module's logic, not on wall-clock ticks.
    monkeypatch.setattr(deepgram_module, "_POLL_SECONDS", 0.005)
    return backend


def _run(coro):
    return asyncio.run(coro)


def _source(*pieces: str):
    async def gen():
        for piece in pieces:
            yield piece

    return gen()


def _say(module: DeepgramModule, text: str, callback=lambda _b: None) -> None:
    _run(module.synthesize_text(text, TTSOptions(), callback))


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "deepgram", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[deepgram\]"):
        DeepgramModule(_BASE)


# --- config ------------------------------------------------------------------


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_DEEPGRAM_KEY", "from-env")
    DeepgramModule({"type": "deepgram", "api_key_env": "MY_DEEPGRAM_KEY"})
    assert fake.client_kwargs == {"api_key": "from-env"}


@pytest.mark.parametrize(
    ("override", "field"),
    [
        # Checked by the module itself: every shape stays.
        ({"sample_rate": 44100}, "sample_rate"),
        ({"sample_rate": 24000.0}, "sample_rate"),
        ({"sample_rate": "24000"}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"mip_opt_out": "yes"}, "mip_opt_out"),
        ({"mip_opt_out": 1}, "mip_opt_out"),
        # Helper-validated fields: one row per field and per bound.
        ({"model": ""}, "model"),
        ({"speed": 0.5}, "speed"),
        ({"speed": 2.0}, "speed"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        DeepgramModule({**_BASE, **override})


def test_defaults_reach_the_socket(fake):
    module = DeepgramModule(_BASE)
    assert module.sample_rate == 24000
    _say(module, "Hi.")
    assert fake.connect_kwargs == [
        {"model": "aura-2-thalia-en", "encoding": "linear16", "sample_rate": 24000}
    ]


def test_custom_values_reach_the_socket(fake):
    module = DeepgramModule(
        {
            **_BASE,
            "model": "aura-2-hector-fr",
            "sample_rate": 16000,
            "speed": 1,
            "mip_opt_out": True,
        }
    )
    assert module.sample_rate == 16000
    _say(module, "Salut.")
    assert fake.connect_kwargs == [
        {
            "model": "aura-2-hector-fr",
            "encoding": "linear16",
            "sample_rate": 16000,
            "speed": 1.0,
            "mip_opt_out": True,
        }
    ]


# --- synthesize_text ---------------------------------------------------------


def test_text_then_flush_then_close_and_audio_unchanged(fake, caplog):
    fake.preamble = [
        types.SimpleNamespace(type="Metadata", request_id="r"),
        types.SimpleNamespace(type="Warning", warn_code="W1", warn_msg="careful"),
    ]
    received: list[bytes] = []
    with caplog.at_level(logging.WARNING, logger="tts_engine.modules.deepgram"):
        _say(DeepgramModule(_BASE), "Hello world.", received.append)

    # Word-boundary grouping applies to a complete text too: the last word is
    # the remainder, sent before the final Flush.
    assert fake.socket.sent == ["Hello ", "world.", "FLUSH", "CLOSE"]
    assert received == [_AUDIO, _AUDIO]  # one per Speak; Metadata, Warning skipped
    assert "W1" in caplog.text and "careful" in caplog.text
    assert fake.socket.closed.is_set()


def test_odd_byte_is_carried_and_audio_is_fed_in_100ms_slices(fake):
    # At 8 kHz a 100 ms slice is 1600 bytes. The first byte waits for the next
    # message (one feeder spans the utterance), whole samples go out in slices
    # of the declared rate, and the odd byte left joins the last message.
    fake.audio_per_text = None
    fake.preamble = [b"\x01", b"\x02" * 3200, b"\x03"]
    received: list[bytes] = []
    _say(DeepgramModule({**_BASE, "sample_rate": 8000}), "Hi.", received.append)
    assert received == [b"\x01" + b"\x02" * 1599, b"\x02" * 1600, b"\x02\x03"]


def test_long_text_is_cut_into_flushed_segments_at_sentence_ends(fake):
    sentence = "The quick brown fox jumps over the lazy dog near the bank. "
    text = sentence * 40  # ~2400 characters: over the server's pending limit
    _say(DeepgramModule(_BASE), text)

    sent = fake.socket.sent
    assert "".join(fake.texts) == text
    assert sent[-2:] == ["FLUSH", "CLOSE"]
    segments = "".join(m if m != "FLUSH" else "|" for m in sent[:-1]).split("|")[:-1]
    assert len(segments) >= 3
    assert all(len(s) <= 900 for s in segments)
    assert all(s.endswith(". ") for s in segments)
    assert fake.socket.max_unanswered_at_flush == 0


def test_word_longer_than_a_segment_is_hard_cut(fake):
    _say(DeepgramModule(_BASE), "x" * 2000)
    assert [len(t) for t in fake.texts] == [900, 900, 200]


async def test_sender_waits_for_flushed_before_the_next_segment(fake):
    fake.hold_flushed = True
    sentence = "The quick brown fox jumps over the lazy dog near the bank. "
    text = sentence * 40
    task = asyncio.create_task(
        DeepgramModule(_BASE).synthesize_text(text, TTSOptions(), lambda _b: None)
    )
    while not fake.sockets or "FLUSH" not in fake.sockets[-1].sent:
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)  # let the sender run as far as the gate allows

    sent = list(fake.socket.sent)
    assert sent.count("FLUSH") == 1
    assert sent[-1] == "FLUSH"  # nothing of the next segment until Flushed

    while not task.done():  # answer each held Flush as the sender reaches it
        fake.socket.release_flushed()
        await asyncio.sleep(0.005)
    await task
    assert "".join(fake.texts) == text
    assert fake.socket.sent[-1] == "CLOSE"


def test_connect_error_is_tts_error(fake):
    fake.raise_on_connect = RuntimeError("HTTP 401")
    with pytest.raises(TTSError, match="Deepgram request failed: HTTP 401"):
        _say(DeepgramModule(_BASE), "Hi.")


def test_server_error_mid_stream_is_tts_error(fake):
    fake.script = [_AUDIO, ConnectionError("1008 DATA-0001")]
    with pytest.raises(TTSError, match="DATA-0001"):
        _say(DeepgramModule(_BASE), "Hi.")


def test_server_closing_before_close_is_tts_error(fake):
    # Two segments and no Flushed: the sender waits at the gate, so the server
    # ends the stream before the module has sent Close.
    fake.script = [_AUDIO]
    with pytest.raises(TTSError, match="closed the connection"):
        _say(DeepgramModule(_BASE), "x" * 2000)


def test_send_error_is_tts_error(fake):
    fake.raise_on_send = ConnectionError("broken pipe")
    with pytest.raises(TTSError, match="broken pipe"):
        _say(DeepgramModule(_BASE), "Hi.")


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(DeepgramModule(_BASE), "Hi.", bad_callback)
    assert fake.socket.closed.wait(1)


async def test_cancel_mid_audio_stops_callbacks_before_raising(fake):
    fake.endless_audio = True
    fake.hold_flushed = True
    first = threading.Event()

    task = asyncio.create_task(
        DeepgramModule(_BASE).synthesize_text(
            "Hi.", TTSOptions(), lambda _b: first.set()
        )
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert await asyncio.to_thread(fake.socket.closed.wait, 1)


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_sends_word_groups_then_flush_and_close(fake):
    _run(
        DeepgramModule(_BASE).synthesize_text_stream(
            _source("Hel", "lo wor", "ld. How", " are you?"),
            TTSOptions(),
            lambda _b: None,
        )
    )
    assert fake.socket.sent == [
        "Hello ",
        "world. ",
        "How are ",
        "you?",
        "FLUSH",
        "CLOSE",
    ]


def test_empty_text_stream_closes_without_text_or_flush(fake):
    received: list[bytes] = []
    _run(
        DeepgramModule(_BASE).synthesize_text_stream(
            _source(), TTSOptions(), received.append
        )
    )
    assert fake.socket.sent == ["CLOSE"]
    assert received == []


async def test_text_stream_feeds_audio_before_the_source_finishes(fake):
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(got_audio.set)

    async def source():
        yield "Hello world. "
        await got_audio.wait()
        yield "Second one."

    await asyncio.wait_for(
        DeepgramModule(_BASE).synthesize_text_stream(source(), TTSOptions(), callback),
        timeout=5,
    )
    assert fake.texts == ["Hello world. ", "Second ", "one."]


async def test_text_stream_source_error_propagates_unchanged(fake):
    async def source():
        yield "Hello "
        raise RuntimeError("LLM failed")

    with pytest.raises(RuntimeError, match="LLM failed"):
        await DeepgramModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: None
        )
    assert await asyncio.to_thread(fake.socket.closed.wait, 1)
    assert "CLOSE" not in fake.socket.sent


async def test_text_stream_cancel_while_the_server_is_silent_stops_promptly(fake):
    first = threading.Event()

    async def source():
        yield "Hi there. "  # one audio chunk, then the source stays open
        await asyncio.Event().wait()

    task = asyncio.create_task(
        DeepgramModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: first.set()
        )
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert await asyncio.to_thread(fake.socket.closed.wait, 1)
    assert "CLOSE" not in fake.socket.sent


# --- connection setup --------------------------------------------------------


async def test_cancel_during_the_handshake_surfaces_promptly_and_closes_late(fake):
    fake.connect_gate = threading.Event()
    task = asyncio.create_task(
        DeepgramModule(_BASE).synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    await asyncio.to_thread(fake.connecting.wait, 5)

    started = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.perf_counter() - started < 1  # not the handshake's wait

    fake.connect_gate.set()  # the handshake completes after the caller left

    # The helper thread finishes the connect after the gate opens, then closes it.
    def opened_then_closed() -> bool:
        deadline = time.perf_counter() + 2
        while not fake.sockets and time.perf_counter() < deadline:
            time.sleep(0.01)
        return bool(fake.sockets) and fake.socket.closed.wait(1)

    assert await asyncio.to_thread(opened_then_closed)
    assert fake.socket.sent == []


def test_handshake_slower_than_timeout_is_tts_error(fake):
    fake.connect_gate = threading.Event()
    with pytest.raises(TTSError, match="not open within"):
        _say(DeepgramModule({**_BASE, "timeout": 0.05}), "Hi.")
    fake.connect_gate.set()


# --- bounded unread audio ----------------------------------------------------


async def test_sender_waits_for_playback_before_admitting_more_text(fake, monkeypatch):
    # The bound is one fake chunk (4 bytes): with the callback blocked on the
    # first chunk, the sender admits text until the audio read fills the
    # bound (plus whatever it sent before the reader caught up), then stops;
    # once playback resumes, the rest goes through. The flush gate is kept
    # out of the way (every Flush answered at once).
    monkeypatch.setattr(deepgram_module, "_MAX_UNREAD_SECONDS", 4 / (2 * 24000))
    release = threading.Event()
    first = threading.Event()

    def callback(_chunk: bytes) -> None:
        first.set()
        release.wait(5)

    text = "Word " * 600
    slices = len(list(slice_text(text, 50)))
    task = asyncio.create_task(
        DeepgramModule(_BASE).synthesize_text(text, TTSOptions(), callback)
    )
    await asyncio.to_thread(first.wait, 5)
    await asyncio.sleep(0.05)  # let the sender run as far as the gate allows
    held = len(fake.texts)
    assert 1 <= held < slices

    release.set()
    await asyncio.wait_for(task, timeout=5)
    # A complete text goes out in slices of at most 50 characters.
    assert len(fake.texts) == slices
    assert all(len(t) <= 50 for t in fake.texts)
    assert "".join(fake.texts) == text


def test_unanswered_flush_with_a_silent_server_is_tts_error(fake):
    fake.hold_flushed = True  # the server never answers the Flush
    sentence = "The quick brown fox jumps over the lazy dog near the bank. "
    with pytest.raises(TTSError, match="no answer within"):
        _say(DeepgramModule({**_BASE, "timeout": 0.05}), sentence * 12)
