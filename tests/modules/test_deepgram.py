"""Unit tests for DeepgramModule with the deepgram SDK faked.

The fast tier never opens a socket, so `deepgram` (and the
`deepgram.speak.v1.types` / `deepgram.speak.v2.types` paths the module imports
`SpeakV1Text` / `SpeakV2Speak` from) is faked via `sys.modules`. The fake
socket answers the way the live API was observed to: nothing until it is
first spoken to; audio after each `Speak`; per `Flush` the answer that marks
the segment generated — `Flushed` on v1; on v2 `Flushed` at once (the turn
started), then `SpeechMetadata` — optionally held back to exercise the
module's gate; per `Clear` / `Interrupt`, optional stale audio then
`Cleared` / `SpeechInterrupted`; `EXCESSIVE_FLUSH` past a flush limit; and a
server-side close on demand or after `Close`.

The module holds one socket for its lifetime, opened at construction
(specs/modules/deepgram.md, "One socket, held for the module's lifetime").
"""

import asyncio
import logging
import queue
import sys
import threading
import time
import types
from collections import deque

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions, slice_text
from tts_engine.modules.deepgram import DeepgramModule
from tts_engine.modules.deepgram import module as deepgram_module

_BASE = {"type": "deepgram", "api_key": "k"}
_FLUX = {**_BASE, "model": "flux-haley-en"}
# Both APIs, for the tests whose behaviour must not depend on the model.
_BOTH = pytest.mark.parametrize("config", [_BASE, _FLUX], ids=["aura", "flux"])
_AUDIO = b"\x01\x00\x02\x00"
_STALE = b"\x09\x00\x09\x00"
_END = object()
_SENTENCE = "The quick brown fox jumps over the lazy dog near the bank. "


class _Text:
    """Stands in for `SpeakV1Text` and `SpeakV2Speak`."""

    def __init__(self, *, text: str) -> None:
        self.text = text


class _Rejected(Exception):
    """Stands in for `websockets.exceptions.InvalidStatus`."""

    def __init__(self, status: int) -> None:
        super().__init__(f"server rejected WebSocket connection: HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status)


class _FakeSocket:
    """The SDK's `V1SocketClient` / `V2SocketClient`: send methods plus a
    blocking message iterator."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self._outbox: queue.Queue = queue.Queue()
        self.sent: list = []  # Speak text, "FLUSH", "CLEAR", "INTERRUPT", "CLOSE"
        self.text_methods: set[str] = set()
        self._close_pending = False
        self.started = threading.Event()  # the server speaks once spoken to
        self.closed = threading.Event()
        self.cancelled = False
        self.flushes_delivered = 0
        self.max_unanswered_at_flush = 0
        self._held_answer = None

    # -- what the module sends ------------------------------------------------

    def send_text(self, message: _Text) -> None:  # v1
        self._speak(message, "send_text")

    def send_speak(self, message: _Text) -> None:  # v2
        self._speak(message, "send_speak")

    def _speak(self, message: _Text, method: str) -> None:
        self._check_send()
        self.started.set()
        self.text_methods.add(method)
        self.sent.append(message.text)
        if self._backend.audio_per_text:
            self._outbox.put(self._backend.audio_per_text)

    def send_flush(self) -> None:
        self._check_send()
        self.started.set()
        unanswered = self.sent.count("FLUSH") - self.flushes_delivered
        self.max_unanswered_at_flush = max(self.max_unanswered_at_flush, unanswered)
        self.sent.append("FLUSH")
        limit = self._backend.flush_limit
        if limit is not None and self.sent.count("FLUSH") > limit:
            self._outbox.put(
                types.SimpleNamespace(
                    type="Warning", warn_code="EXCESSIVE_FLUSH", warn_msg="limit"
                )
            )
            return
        if self._backend.api == "v2":
            # Flux confirms the flush when it starts the turn, before the audio.
            self._outbox.put(types.SimpleNamespace(type="Flushed", speech_id="s"))
        if self._backend.hold_flushed is None:
            self._outbox.put(self._backend.segment_done())
        else:
            self._backend.held += 1

    def send_clear(self) -> None:  # v1
        self._cancel("CLEAR", "Cleared")

    def send_interrupt(self) -> None:  # v2
        self._cancel("INTERRUPT", "SpeechInterrupted")

    def _cancel(self, label: str, answer: str) -> None:
        self._check_send()
        self.sent.append(label)
        self.cancelled = True
        # What the server had in flight arrives before the answer.
        for chunk in self._backend.stale_on_cancel:
            self._outbox.put(chunk)
        if self._backend.held and self._backend.stale_answer_on_cancel:
            self._outbox.put(self._backend.segment_done())
        self._backend.held = 0
        message = types.SimpleNamespace(type=answer)
        if self._backend.hold_cancel_answer:
            self._held_answer = message
        else:
            self._outbox.put(message)

    def send_close(self) -> None:
        self._check_send()
        self.sent.append("CLOSE")
        # The server closes once it has answered every Flush, as live.
        if self._backend.held:
            self._close_pending = True
        else:
            self._outbox.put(_END)

    def _check_send(self) -> None:
        if self._backend.raise_on_send is not None:
            raise self._backend.raise_on_send
        if self.closed.is_set():
            raise ConnectionError("socket closed")

    # -- what the server sends ------------------------------------------------

    def release_flushed(self, audio: bytes | None = None) -> None:
        for _ in range(self._backend.held):
            if audio is not None:
                self._outbox.put(audio)
            self._outbox.put(self._backend.segment_done())
        self._backend.held = 0
        if self._close_pending:
            self._outbox.put(_END)

    def release_cancel_answer(self) -> None:
        self._outbox.put(self._held_answer)

    def server_send(self, message) -> None:
        self.started.set()
        self._outbox.put(message)

    def server_close(self) -> None:
        """The server ends the connection (an idle close, the session cap)."""
        self.started.set()
        self._outbox.put(_END)

    def __iter__(self):
        while not self.started.wait(0.001):
            if self.closed.is_set():
                return
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
            if (
                self._backend.endless_audio
                and not self.cancelled
                and self._outbox.empty()
            ):
                if self.closed.wait(0.001):
                    return
                yield _AUDIO
                continue
            message = self._outbox.get()
            if message is _END:
                return
            if isinstance(message, Exception):
                raise message
            if getattr(message, "type", None) == self._backend.segment_done().type:
                self.flushes_delivered += 1
            yield message


class _FakeConnection:
    """What `speak.v1.connect(...)` returns: a context manager over the socket."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self.socket: _FakeSocket | None = None

    def __enter__(self) -> _FakeSocket:
        if self._backend.raise_on_connect is not None:
            raise self._backend.raise_on_connect
        if self._backend.connect_gate is not None:
            self._backend.connecting.set()
            self._backend.connect_gate.wait(5)  # a slow handshake
        self.socket = _FakeSocket(self._backend)
        self._backend.sockets.append(self.socket)
        return self.socket

    def __exit__(self, *_exc) -> bool:
        if self._backend.exit_gate is not None:
            self._backend.exit_gate.wait(5)  # a slow close handshake
        assert self.socket is not None
        self.socket.closed.set()
        self.socket._outbox.put(_END)
        return False


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.connect_kwargs: list[dict] = []
        self.api: str | None = None  # "v1" or "v2", set by the last connect
        self.sockets: list[_FakeSocket] = []
        self.preamble: list = []  # messages the server sends when first spoken to
        self.script: list | None = None
        self.audio_per_text: bytes | None = _AUDIO
        self.endless_audio = False
        self.hold_flushed: bool | None = None
        self.held = 0
        self.flush_limit: int | None = None  # EXCESSIVE_FLUSH past this many
        self.stale_on_cancel: list[bytes] = []
        self.stale_answer_on_cancel = False
        self.hold_cancel_answer = False
        self.raise_on_connect: Exception | None = None
        self.raise_on_send: Exception | None = None
        self.connect_gate: threading.Event | None = None  # holds the handshake
        self.exit_gate: threading.Event | None = None  # holds the close
        self.connecting = threading.Event()

    def segment_done(self) -> types.SimpleNamespace:
        """The answer marking a flushed segment generated, per API."""
        if self.api == "v2":
            return types.SimpleNamespace(type="SpeechMetadata", speech_id="s")
        return types.SimpleNamespace(type="Flushed")

    @property
    def connects(self) -> int:
        return len(self.connect_kwargs)

    @property
    def socket(self) -> _FakeSocket:
        return self.sockets[-1]

    @property
    def texts(self) -> list[str]:
        return [
            m
            for m in self.socket.sent
            if m not in ("FLUSH", "CLOSE", "CLEAR", "INTERRUPT")
        ]


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class Api:
        def __init__(self, name: str) -> None:
            self._name = name

        def connect(self, **kwargs):
            backend.api = self._name
            backend.connect_kwargs.append(kwargs)
            return _FakeConnection(backend)

    class DeepgramClient:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.speak = types.SimpleNamespace(v1=Api("v1"), v2=Api("v2"))

    package = types.ModuleType("deepgram")
    package.DeepgramClient = DeepgramClient  # type: ignore[attr-defined]
    speak_types = types.ModuleType("deepgram.speak.v1.types")
    speak_types.SpeakV1Text = _Text  # type: ignore[attr-defined]
    speak_v2_types = types.ModuleType("deepgram.speak.v2.types")
    speak_v2_types.SpeakV2Speak = _Text  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepgram", package)
    monkeypatch.setitem(sys.modules, "deepgram.speak", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "deepgram.speak.v1", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "deepgram.speak.v1.types", speak_types)
    monkeypatch.setitem(sys.modules, "deepgram.speak.v2", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "deepgram.speak.v2.types", speak_v2_types)
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


def _wait(condition, timeout: float = 1.0) -> bool:
    deadline = time.perf_counter() + timeout
    while not condition():
        if time.perf_counter() > deadline:
            return False
        time.sleep(0.002)
    return True


def _held(module: DeepgramModule):
    socket = module._socket
    assert socket is not None
    return socket


async def _cancel_once_audio_flows(module, text="Hi.", stream=False):
    """Start an utterance, cancel it once audio reached the callback."""
    first = threading.Event()
    if stream:

        async def source():
            yield "Hi there. "
            await asyncio.Event().wait()  # the source stays open

        coro = module.synthesize_text_stream(
            source(), TTSOptions(), lambda _b: first.set()
        )
    else:
        coro = module.synthesize_text(text, TTSOptions(), lambda _b: first.set())
    task = asyncio.create_task(coro)
    assert await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


def _cancel_label(config: dict) -> str:
    return "INTERRUPT" if config is _FLUX else "CLEAR"


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
        # Flux (speak v2): its own speed range and step, expressivity.
        ({"model": "flux-haley-en", "speed": 0.45}, "speed"),
        ({"model": "flux-haley-en", "speed": 0.53}, "speed"),
        ({"model": "flux-haley-en", "expressivity": 3}, "expressivity"),
        ({"model": "flux-haley-en", "expressivity": 1.0}, "expressivity"),
        ({"model": "flux-haley-en", "expressivity": True}, "expressivity"),
        ({"expressivity": 1}, "requires a Flux model"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        DeepgramModule({**_BASE, **override})
    assert fake.connects == 0  # validated before any handshake


def test_defaults_reach_the_socket(fake):
    module = DeepgramModule(_BASE)
    assert module.sample_rate == 24000
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
    assert fake.connect_kwargs == [
        {
            "model": "aura-2-hector-fr",
            "encoding": "linear16",
            "sample_rate": 16000,
            "speed": 1.0,
            "mip_opt_out": True,
        }
    ]


# --- construction ------------------------------------------------------------


def test_construction_connects_once_before_any_say(fake):
    DeepgramModule(_BASE)
    assert fake.connects == 1
    assert fake.socket.sent == []


@pytest.mark.parametrize("status", [400, 401, 403])
def test_rejected_handshake_at_construction_is_config_error(fake, status):
    fake.raise_on_connect = _Rejected(status)
    with pytest.raises(ConfigError, match=f"HTTP {status}"):
        DeepgramModule(_BASE)


@pytest.mark.parametrize(
    "error", [_Rejected(429), RuntimeError("network down")], ids=["429", "network"]
)
def test_other_handshake_failure_at_construction_is_tts_error(fake, error):
    fake.raise_on_connect = error
    with pytest.raises(TTSError, match="Deepgram request failed"):
        DeepgramModule(_BASE)


def test_handshake_slower_than_timeout_is_tts_error(fake):
    fake.connect_gate = threading.Event()
    with pytest.raises(TTSError, match="not open within"):
        DeepgramModule({**_BASE, "timeout": 0.05})
    fake.connect_gate.set()


# --- synthesize_text ---------------------------------------------------------


def test_text_then_flush_and_audio_unchanged(fake, caplog):
    fake.preamble = [
        types.SimpleNamespace(type="Metadata", request_id="r"),
        types.SimpleNamespace(type="Warning", warn_code="W1", warn_msg="careful"),
    ]
    received: list[bytes] = []
    with caplog.at_level(logging.WARNING, logger="tts_engine.modules.deepgram"):
        _say(DeepgramModule(_BASE), "Hello world.", received.append)

    # Word-boundary grouping applies to a complete text too: the last word is
    # the remainder, sent before the final Flush. No Close: the socket stays.
    assert fake.socket.sent == ["Hello ", "world.", "FLUSH"]
    assert received == [_AUDIO, _AUDIO]  # one per Speak; Metadata, Warning skipped
    assert "W1" in caplog.text and "careful" in caplog.text
    assert not fake.socket.closed.is_set()


def test_blank_text_sends_nothing(fake):
    module = DeepgramModule(_BASE)
    _say(module, "  \n")
    assert fake.socket.sent == []
    assert fake.connects == 1


def test_odd_byte_is_carried_and_audio_is_fed_in_100ms_slices(fake):
    # At 8 kHz a 100 ms slice is 1600 bytes. The first byte waits for the next
    # message (one feeder spans the utterance), whole samples go out in slices
    # of the declared rate, and the odd byte left joins the last message.
    fake.audio_per_text = None
    fake.preamble = [b"\x01", b"\x02" * 3200, b"\x03"]
    received: list[bytes] = []
    _say(DeepgramModule({**_BASE, "sample_rate": 8000}), "Hi.", received.append)
    assert received == [b"\x01" + b"\x02" * 1599, b"\x02" * 1600, b"\x02\x03"]


@_BOTH
def test_long_text_is_cut_into_flushed_segments_at_sentence_ends(fake, config):
    text = _SENTENCE * 40  # ~2400 characters: over the server's pending limit
    _say(DeepgramModule(config), text)

    sent = fake.socket.sent
    assert "".join(fake.texts) == text
    assert sent[-1] == "FLUSH"
    segments = "".join(m if m != "FLUSH" else "|" for m in sent).split("|")[:-1]
    assert len(segments) >= 3
    assert all(len(s) <= 900 for s in segments)
    assert all(s.endswith(". ") for s in segments)
    assert fake.socket.max_unanswered_at_flush == 0


def test_word_longer_than_a_segment_is_hard_cut(fake):
    _say(DeepgramModule(_BASE), "x" * 2000)
    assert [len(t) for t in fake.texts] == [900, 900, 200]


@_BOTH
async def test_sender_waits_for_the_segment_before_the_next_one(fake, config):
    # v1: until `Flushed`; v2: until `SpeechMetadata` — its `Flushed`, sent at
    # once by the fake as by the live API, must not open the gate.
    fake.hold_flushed = True
    text = _SENTENCE * 40
    module = DeepgramModule(config)
    task = asyncio.create_task(
        module.synthesize_text(text, TTSOptions(), lambda _b: None)
    )
    while "FLUSH" not in fake.socket.sent:
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)  # let the sender run as far as the gate allows

    sent = list(fake.socket.sent)
    assert sent.count("FLUSH") == 1
    assert sent[-1] == "FLUSH"  # nothing of the next segment until it is done

    while not task.done():  # answer each held Flush as the sender reaches it
        fake.socket.release_flushed()
        await asyncio.sleep(0.005)
    await task
    assert "".join(fake.texts) == text
    assert fake.socket.sent[-1] == "FLUSH"


def test_server_error_mid_stream_is_tts_error(fake):
    fake.script = [_AUDIO, ConnectionError("1008 DATA-0001")]
    module = DeepgramModule(_BASE)
    with pytest.raises(TTSError, match="DATA-0001"):
        _say(module, "Hi.")
    assert _held(module).dead.is_set()


def test_server_closing_during_an_utterance_is_tts_error_then_reconnects(fake):
    # Two segments and no Flushed: the sender waits at the gate, so the server
    # ends the stream while the utterance still has text to send.
    fake.script = [_AUDIO]
    module = DeepgramModule(_BASE)
    with pytest.raises(TTSError, match="closed the connection"):
        _say(module, "x" * 2000)
    fake.script = None
    received: list[bytes] = []
    _say(module, "Hi.", received.append)
    assert fake.connects == 2
    assert received == [_AUDIO]


def test_send_error_is_tts_error(fake):
    fake.raise_on_send = ConnectionError("broken pipe")
    module = DeepgramModule(_BASE)
    with pytest.raises(TTSError, match="broken pipe"):
        _say(module, "Hi.")
    assert _held(module).dead.is_set()


def test_callback_error_propagates_unchanged_and_clears(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    module = DeepgramModule(_BASE)
    with pytest.raises(ValueError, match="playback failed"):
        _say(module, "Hi.", bad_callback)
    assert fake.socket.sent[-1] == "CLEAR"
    assert not fake.socket.closed.is_set()
    _say(module, "Again.")  # the same socket serves the next utterance
    assert fake.connects == 1


# --- held socket -------------------------------------------------------------


@_BOTH
def test_two_utterances_share_one_socket(fake, config):
    module = DeepgramModule(config)
    received: list[bytes] = []
    _say(module, "Hello.", received.append)
    _say(module, "Again.", received.append)
    assert fake.connects == 1
    assert fake.socket.sent == ["Hello.", "FLUSH", "Again.", "FLUSH"]
    assert received == [_AUDIO, _AUDIO]


def test_socket_closed_while_idle_is_reconnected_by_the_next_utterance(fake):
    module = DeepgramModule(_BASE)
    old = fake.socket
    old.server_close()
    assert _wait(_held(module).dead.is_set)
    received: list[bytes] = []
    _say(module, "Hi.", received.append)
    assert fake.connects == 2
    assert fake.socket is not old and fake.socket.sent == ["Hi.", "FLUSH"]
    assert received == [_AUDIO]


def test_reconnect_failure_fails_that_utterance_only(fake):
    module = DeepgramModule(_BASE)
    fake.socket.server_close()
    assert _wait(_held(module).dead.is_set)
    fake.raise_on_connect = RuntimeError("network down")
    with pytest.raises(TTSError, match="network down"):
        _say(module, "Hi.")
    fake.raise_on_connect = None
    _say(module, "Hi.")
    assert fake.connects == 3


@pytest.mark.parametrize(
    ("shift", "replaced"),
    [
        # (what to move back, by how much on each clock), and whether the next
        # utterance gets a new socket.
        ({"opened": 56 * 60.0}, True),  # near Flux's one-hour session cap
        ({"opened": 54 * 60.0}, False),
        ({"last_mono": 541.0, "last_wall": 541.0}, True),  # idle too long
        ({"last_mono": 539.0, "last_wall": 539.0}, False),
        ({"last_wall": 21.0}, True),  # the host slept through a ping
        ({"last_wall": 19.0}, False),
    ],
    ids=["old", "young", "idle", "not-idle", "slept", "not-slept"],
)
def test_socket_replaced_before_the_utterance_when_old_idle_or_slept(
    fake, shift, replaced
):
    module = DeepgramModule(_BASE)
    old = fake.socket
    socket = _held(module)
    for field, seconds in shift.items():
        setattr(socket, field, getattr(socket, field) - seconds)
    _say(module, "Hi.")
    assert fake.connects == (2 if replaced else 1)
    if replaced:
        assert old.sent == []  # nothing sent on the replaced socket
        assert _wait(old.closed.is_set)  # closed off the worker
        assert fake.socket.sent == ["Hi.", "FLUSH"]


def test_messages_sent_and_received_refresh_the_idle_clocks(fake):
    module = DeepgramModule(_BASE)
    socket = _held(module)
    socket.last_mono -= 100
    socket.last_wall -= 100
    socket.send(lambda: None)
    assert time.monotonic() - socket.last_mono < 1
    assert time.time() - socket.last_wall < 1

    socket.last_mono -= 100
    socket.last_wall -= 100
    fake.socket.server_send(types.SimpleNamespace(type="Metadata"))
    assert _wait(lambda: time.monotonic() - socket.last_mono < 1)
    assert time.time() - socket.last_wall < 1


@pytest.mark.parametrize(
    ("config", "flushes", "replaced"),
    [(_BASE, 16, True), (_BASE, 15, False), (_FLUX, 16, False)],
    ids=["aura-spent", "aura-left", "flux"],
)
def test_v1_socket_replaced_once_its_flush_budget_is_spent(
    fake, config, flushes, replaced
):
    module = DeepgramModule(config)
    now = time.monotonic()
    _held(module).flush_times = deque([now - 30.0] * flushes)
    _say(module, "Hi.")
    assert fake.connects == (2 if replaced else 1)


def test_flushes_older_than_the_window_do_not_count(fake):
    module = DeepgramModule(_BASE)
    _held(module).flush_times = deque([time.monotonic() - 61.0] * 20)
    _say(module, "Hi.")
    assert fake.connects == 1
    assert len(_held(module).flush_times) == 1  # pruned, plus this utterance's


@pytest.mark.parametrize(
    ("config", "chars", "age", "replaced"),
    [
        (_BASE, 1200, 30.0, True),
        (_BASE, 1199, 30.0, False),
        (_BASE, 2400, 61.0, False),  # out of the window: not counted
        (_FLUX, 1200, 30.0, True),
    ],
    ids=["aura-spent", "aura-left", "aura-old", "flux-spent"],
)
def test_socket_replaced_once_its_character_budget_is_spent(
    fake, config, chars, age, replaced
):
    module = DeepgramModule(config)
    _held(module).char_log = deque([(time.monotonic() - age, chars)])
    _say(module, "Hi.")
    assert fake.connects == (2 if replaced else 1)


def test_sent_text_is_counted_against_the_character_budget(fake):
    module = DeepgramModule(_BASE)
    _say(module, "Hello world.")
    assert sum(n for _, n in _held(module).char_log) == len("Hello world.")


async def test_cancel_during_a_reconnect_surfaces_promptly_and_closes_late(fake):
    module = DeepgramModule(_BASE)
    fake.socket.server_close()
    assert await asyncio.to_thread(_wait, _held(module).dead.is_set)
    fake.connect_gate = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    await asyncio.to_thread(fake.connecting.wait, 5)

    started = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.perf_counter() - started < 1  # not the handshake's wait

    fake.connect_gate.set()  # the handshake completes after the caller left
    # The helper thread finishes the connect after the gate opens, then closes it.
    assert await asyncio.to_thread(_wait, lambda: len(fake.sockets) == 2, 2)
    assert await asyncio.to_thread(fake.socket.closed.wait, 1)
    assert fake.socket.sent == []


def test_v2_error_while_idle_is_logged_and_the_next_utterance_reconnects(fake, caplog):
    module = DeepgramModule(_FLUX)
    error = types.SimpleNamespace(type="Error", code="NET-0004", description="idle")
    with caplog.at_level(logging.DEBUG, logger="tts_engine.modules.deepgram"):
        fake.socket.server_send(error)
        fake.socket.server_close()
        assert _wait(_held(module).dead.is_set)
    assert "Error" in caplog.text
    _say(module, "Hi.")
    assert fake.connects == 2


# --- cancellation ------------------------------------------------------------


@_BOTH
async def test_cancel_mid_audio_stops_callbacks_and_keeps_the_socket(fake, config):
    fake.endless_audio = True
    fake.hold_flushed = True
    module = DeepgramModule(config)
    await _cancel_once_audio_flows(module)
    await asyncio.sleep(0.05)  # a sender racing the cancel would send now
    assert fake.socket.sent[-1] == _cancel_label(config)  # nothing after it
    assert not fake.socket.closed.is_set()
    assert fake.connects == 1


@_BOTH
async def test_stale_audio_before_the_cancel_answer_never_reaches_the_next_utterance(
    fake, config
):
    # The cancelled utterance's audio and its finished-segment answer arrive
    # after the cancel, before its answer: none of it is the next utterance's.
    fake.hold_flushed = True
    fake.stale_on_cancel = [_STALE, _STALE]
    fake.stale_answer_on_cancel = True
    module = DeepgramModule(config)
    await _cancel_once_audio_flows(module)

    fake.hold_flushed = None
    received: list[bytes] = []
    await module.synthesize_text("Again.", TTSOptions(), received.append)
    assert received == [_AUDIO]
    assert fake.connects == 1
    assert fake.socket.sent[-2:] == ["Again.", "FLUSH"]


@_BOTH
async def test_next_utterance_waits_for_the_cancel_answer(fake, config):
    fake.hold_flushed = True
    fake.hold_cancel_answer = True
    module = DeepgramModule(config)
    await _cancel_once_audio_flows(module)

    fake.hold_flushed = None
    task = asyncio.create_task(
        module.synthesize_text("Again.", TTSOptions(), lambda _b: None)
    )
    await asyncio.sleep(0.05)
    assert "Again." not in fake.socket.sent  # waiting for the answer
    fake.socket.release_cancel_answer()
    await asyncio.wait_for(task, timeout=5)
    assert fake.socket.sent[-2:] == ["Again.", "FLUSH"]
    assert fake.connects == 1


async def test_unanswered_cancel_drops_the_socket(fake):
    fake.hold_flushed = True
    fake.hold_cancel_answer = True
    module = DeepgramModule({**_BASE, "timeout": 0.05})
    await _cancel_once_audio_flows(module)
    old = fake.socket

    fake.hold_flushed = None
    await module.synthesize_text("Again.", TTSOptions(), lambda _b: None)
    assert fake.connects == 2
    assert await asyncio.to_thread(old.closed.wait, 1)
    assert fake.socket.sent == ["Again.", "FLUSH"]


def test_a_send_after_stop_sends_nothing(fake):
    # The sender re-checks `stop` under the socket's send lock, so no text
    # can follow the cancel message onto the socket.
    module = DeepgramModule(_BASE)
    stop = threading.Event()
    stop.set()
    sent = []
    assert _held(module).send(sent.append, "late", stop=stop) is False
    assert sent == []


# --- flush rate limit (v1) ---------------------------------------------------


def test_refused_final_flush_closes_and_completes_the_utterance(fake, caplog):
    fake.flush_limit = 0  # every Flush is answered EXCESSIVE_FLUSH
    module = DeepgramModule(_BASE)
    received: list[bytes] = []
    with caplog.at_level(logging.WARNING, logger="tts_engine.modules.deepgram"):
        _say(module, "Hi.", received.append)
    assert received == [_AUDIO]
    assert fake.socket.sent == ["Hi.", "FLUSH", "CLOSE"]
    assert "EXCESSIVE_FLUSH" in caplog.text

    fake.flush_limit = None
    _say(module, "Again.")
    assert fake.connects == 2  # an exhausted socket is replaced


def test_refused_mid_utterance_flush_is_tts_error_and_drops_the_socket(fake):
    fake.flush_limit = 0
    module = DeepgramModule(_BASE)
    old = fake.socket
    with pytest.raises(TTSError, match="flush rate limit"):
        _say(module, "x" * 2000)
    assert _wait(old.closed.is_set)
    fake.flush_limit = None
    _say(module, "Hi.")
    assert fake.connects == 2


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_sends_word_groups_then_flush(fake):
    _run(
        DeepgramModule(_BASE).synthesize_text_stream(
            _source("Hel", "lo wor", "ld. How", " are you?"),
            TTSOptions(),
            lambda _b: None,
        )
    )
    assert fake.socket.sent == ["Hello ", "world. ", "How are ", "you?", "FLUSH"]


def test_empty_text_stream_sends_nothing(fake):
    received: list[bytes] = []
    _run(
        DeepgramModule(_BASE).synthesize_text_stream(
            _source(), TTSOptions(), received.append
        )
    )
    assert fake.socket.sent == []
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


@_BOTH
async def test_text_stream_source_error_propagates_unchanged(fake, config):
    async def source():
        yield "Hello "
        raise RuntimeError("LLM failed")

    with pytest.raises(RuntimeError, match="LLM failed"):
        await DeepgramModule(config).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: None
        )
    assert fake.socket.sent[-1] == _cancel_label(config)
    assert "CLOSE" not in fake.socket.sent
    assert not fake.socket.closed.is_set()


async def test_text_stream_cancel_while_the_server_is_silent_stops_promptly(fake):
    module = DeepgramModule(_BASE)
    await _cancel_once_audio_flows(module, stream=True)
    assert fake.socket.sent[-1] == "CLEAR"
    assert "CLOSE" not in fake.socket.sent
    assert not fake.socket.closed.is_set()


# --- Flux TTS (speak v2) -----------------------------------------------------


def test_flux_model_runs_on_speak_v2(fake):
    module = DeepgramModule(
        {**_FLUX, "speed": 0.5, "expressivity": -2, "mip_opt_out": False}
    )
    _run(
        module.synthesize_text_stream(
            _source("Hel", "lo wor", "ld. How", " are you?"),
            TTSOptions(),
            lambda _b: None,
        )
    )
    assert fake.api == "v2"
    assert fake.connect_kwargs == [
        {
            "model": "flux-haley-en",
            "encoding": "linear16",
            "sample_rate": 24000,
            "speed": 0.5,
            "expressivity": -2,
            "mip_opt_out": False,
        }
    ]
    assert fake.socket.text_methods == {"send_speak"}
    # Flux joins a turn's Speak texts verbatim: the word groups keep their
    # spaces, so they concatenate back to the source text.
    assert fake.socket.sent[-1] == "FLUSH"
    assert "".join(fake.texts) == "Hello world. How are you?"


def test_aura_model_runs_on_speak_v1(fake):
    _say(DeepgramModule(_BASE), "Hi.")
    assert fake.api == "v1"
    assert fake.socket.text_methods == {"send_text"}


async def test_flux_utterance_ends_on_speech_metadata_not_flushed(fake):
    # Flux's `Flushed` arrives before the turn's audio: the utterance must
    # keep reading until `SpeechMetadata`, and feed the audio in between.
    fake.audio_per_text = None
    fake.hold_flushed = True
    received: list[bytes] = []
    module = DeepgramModule(_FLUX)
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), received.append)
    )
    while "FLUSH" not in fake.socket.sent:
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)
    assert not task.done()

    fake.socket.release_flushed(audio=_AUDIO)
    await asyncio.wait_for(task, timeout=5)
    assert received == [_AUDIO]


def test_flux_error_event_is_tts_error(fake, caplog):
    fake.script = [
        types.SimpleNamespace(type="Connected", request_id="r"),
        types.SimpleNamespace(
            type="Warning", code="SYNTHESIS_RETRYING", description="retrying"
        ),
        _AUDIO,
        types.SimpleNamespace(
            type="Error", code="DATA-0002", description="invalid control"
        ),
    ]
    with (
        caplog.at_level(logging.WARNING, logger="tts_engine.modules.deepgram"),
        pytest.raises(TTSError, match="DATA-0002 invalid control"),
    ):
        _say(DeepgramModule(_FLUX), "Hi.")
    assert "SYNTHESIS_RETRYING" in caplog.text


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
    module = DeepgramModule(_BASE)
    task = asyncio.create_task(module.synthesize_text(text, TTSOptions(), callback))
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


async def test_unread_count_starts_at_zero_after_an_aborted_utterance(
    fake, monkeypatch
):
    # The aborted utterance leaves unread audio in its inbox; with the bound at
    # one chunk, the next utterance would stall at its gate if it inherited it.
    monkeypatch.setattr(deepgram_module, "_MAX_UNREAD_SECONDS", 4 / (2 * 24000))
    fake.hold_flushed = True
    fake.stale_on_cancel = [_STALE] * 5
    module = DeepgramModule(_BASE)
    await _cancel_once_audio_flows(module)
    fake.hold_flushed = None
    await asyncio.wait_for(
        module.synthesize_text("One two three.", TTSOptions(), lambda _b: None),
        timeout=5,
    )
    assert fake.socket.sent[-1] == "FLUSH"


def test_unanswered_flush_with_a_silent_server_is_tts_error_and_drops(fake):
    fake.hold_flushed = True  # the server never answers the Flush
    module = DeepgramModule({**_BASE, "timeout": 0.05})
    old = fake.socket
    with pytest.raises(TTSError, match="no answer within"):
        _say(module, _SENTENCE * 12)
    assert _wait(old.closed.is_set)
    fake.hold_flushed = None
    _say(module, "Hi.")
    assert fake.connects == 2


# --- close -------------------------------------------------------------------


def test_close_closes_the_held_socket_off_the_calling_thread(fake):
    module = DeepgramModule(_BASE)
    fake.exit_gate = threading.Event()  # a slow close handshake
    started = time.perf_counter()
    module.close()
    assert time.perf_counter() - started < 0.5
    assert not fake.socket.closed.is_set()
    fake.exit_gate.set()
    assert fake.socket.closed.wait(1)
    assert module._socket is None


# --- voice listing -----------------------------------------------------------


def _fake_models_sdk(monkeypatch, tts_models) -> dict:
    calls: dict = {}

    class Models:
        def list(self, **kwargs):
            calls["list"] = kwargs
            return types.SimpleNamespace(stt=[object()], tts=tts_models)

    class DeepgramClient:
        def __init__(self, **kwargs) -> None:
            calls["client"] = kwargs
            self.manage = types.SimpleNamespace(
                v1=types.SimpleNamespace(models=Models())
            )

    package = types.ModuleType("deepgram")
    package.DeepgramClient = DeepgramClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepgram", package)
    return calls


def test_list_voices_selects_each_voice_by_its_model(monkeypatch):
    apollo = types.SimpleNamespace(
        canonical_name="aura-2-apollo-en",
        name="apollo",
        languages=["en", "en-US"],
        metadata=types.SimpleNamespace(
            accent="American",
            age="Adult",
            tags=["masculine", "confident", "casual"],
            use_cases=["casual chat"],
        ),
    )
    bare = types.SimpleNamespace(
        canonical_name="aura-x", name=None, languages=None, metadata=None
    )
    nameless = types.SimpleNamespace(
        canonical_name=None, name="?", languages=None, metadata=None
    )
    calls = _fake_models_sdk(monkeypatch, [apollo, bare, nameless])

    voices = DeepgramModule.list_voices(
        {"type": "deepgram", "api_key": "k", "timeout": 10}
    )

    assert calls["client"] == {"api_key": "k"}
    assert calls["list"] == {"request_options": {"timeout_in_seconds": 10}}
    first, second = voices  # the entry with no canonical name is skipped
    assert first.config == {"model": "aura-2-apollo-en"}
    assert (first.name, first.languages, first.gender, first.age) == (
        "apollo",
        ("en", "en-US"),
        "male",
        "Adult",
    )
    assert first.tags == ("American", "confident", "casual", "casual chat")
    assert (second.id, second.name, second.gender, second.tags) == (
        "aura-x",
        "aura-x",
        None,
        (),
    )
