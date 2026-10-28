"""Unit tests for CartesiaModule with the cartesia SDK faked.

The fast tier never opens a socket, so `cartesia` is faked via `sys.modules`.
The fake connection answers the way the live API was observed to: a chunk of
audio per push, `done` after `no_more_inputs` or after `cancel` of a context
not yet done, and `TimeoutError` from `recv_bytes` while the server is silent.
Hooks let a test script early `done`s (context expiry), `error` events, events
of foreign contexts, connection failures and a server-side close. The module
connects at construction and holds its socket, so `fake.connects` counts the
handshakes and every socket left open is closed at teardown.
"""

import asyncio
import queue
import sys
import threading
import time
import types
from collections.abc import Iterator

import pytest
from websockets.exceptions import InvalidStatus

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions, slice_text
from tts_engine.modules.cartesia import CartesiaModule
from tts_engine.modules.cartesia import module as cartesia_module

_BASE = {"type": "cartesia", "api_key": "k", "voice_id": "voice-1"}
_AUDIO = b"\x01\x00\x02\x00"


def _event(kind: str, context_id: str | None, **fields):
    return types.SimpleNamespace(type=kind, context_id=context_id, **fields)


class _FakeContext:
    """The SDK's `WebSocketContext`: push, no_more_inputs, cancel."""

    def __init__(self, ws: "_FakeWS", context_id: str, kwargs: dict) -> None:
        self._ws = ws
        self.context_id = context_id
        self.kwargs = kwargs
        self.pushed: list[str] = []
        self.ended = False
        self.cancelled = False

    def push(self, text: str) -> None:
        self._ws.check_send()
        self.pushed.append(text)
        backend = self._ws.backend
        if backend.on_push is not None:
            backend.on_push(self, text)
        elif backend.audio_per_push is not None:
            for chunk in backend.audio_per_push:
                self._ws.server_send(_event("chunk", self.context_id, audio=chunk))

    def no_more_inputs(self) -> None:
        self._ws.check_send()
        self.ended = True
        backend = self._ws.backend
        if backend.on_end is not None:
            backend.on_end(self)
        else:
            self._ws.server_send(_event("done", self.context_id))

    def cancel(self) -> None:
        self._ws.check_send()
        self.cancelled = True
        backend = self._ws.backend
        if backend.on_cancel is not None:
            backend.on_cancel(self)
        elif self.context_id not in self._ws.done_sent:
            # Live: a cancelled context is answered with `done`.
            self._ws.server_send(_event("done", self.context_id))


class _FakeWS:
    """The SDK's `TTSResourceConnection`: contexts plus a polled receive."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self.backend = backend
        self._outbox: queue.Queue = queue.Queue()
        self.contexts: list[_FakeContext] = []
        self.done_sent: set[str] = set()
        self.closed = threading.Event()

    def context(self, *, context_id: str, **kwargs) -> _FakeContext:
        ctx = _FakeContext(self, context_id, kwargs)
        self.contexts.append(ctx)
        return ctx

    def check_send(self) -> None:
        if self.backend.raise_on_send is not None:
            raise self.backend.raise_on_send
        if self.closed.is_set():
            raise ConnectionError("socket closed")

    def server_send(self, item) -> None:
        if getattr(item, "type", None) == "done":
            self.done_sent.add(item.context_id)
        self._outbox.put(item)

    def server_close(self) -> None:
        """The server's idle close: the reader sees it, later sends fail."""
        self._outbox.put(ConnectionError("1000 (OK) connection idle timeout"))
        self.closed.set()

    def recv_bytes(self, timeout: float | None = None):
        if self.closed.is_set() and self._outbox.empty():
            raise ConnectionError("socket closed")
        ctx = self.contexts[-1] if self.contexts else None
        if self.backend.endless_audio and ctx is not None and not ctx.cancelled:
            if self.closed.wait(0.001):
                raise ConnectionError("socket closed")
            # Named before the wait: a chunk is always its own context's.
            return _event("chunk", ctx.context_id, audio=_AUDIO)
        try:
            item = self._outbox.get(timeout=timeout)
        except queue.Empty:
            if self.closed.is_set():
                raise ConnectionError("socket closed") from None
            raise TimeoutError from None
        if isinstance(item, Exception):
            raise item
        return item

    def parse_event(self, raw):
        return raw  # the fake already hands out parsed events


class _FakeManager:
    """What `tts.websocket_connect()` returns: a context manager over the socket."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self._ws: _FakeWS | None = None

    def __enter__(self) -> _FakeWS:
        if self._backend.raise_on_connect is not None:
            raise self._backend.raise_on_connect
        if self._backend.connect_gate is not None:
            self._backend.connecting.set()
            self._backend.connect_gate.wait(5)  # a slow handshake
        self._ws = _FakeWS(self._backend)
        self._backend.sockets.append(self._ws)
        return self._ws

    def __exit__(self, *_exc) -> None:
        if self._ws is not None:
            self._ws.closed.set()


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.client_closed = False
        self.connect_kwargs: list[dict] = []
        self.sockets: list[_FakeWS] = []
        self.audio_per_push: list[bytes] | None = [_AUDIO]
        self.on_push = None  # (ctx, text) -> None, replaces the default answer
        self.on_end = None  # (ctx) -> None, replaces the default `done`
        self.on_cancel = None  # (ctx) -> None, replaces the default `done`
        self.endless_audio = False
        self.raise_on_connect: Exception | None = None
        self.raise_on_send: Exception | None = None
        self.connect_gate: threading.Event | None = None  # holds the handshake
        self.connecting = threading.Event()

    @property
    def ws(self) -> _FakeWS:
        return self.sockets[-1]

    @property
    def connects(self) -> int:
        return len(self.connect_kwargs)


@pytest.fixture
def fake(monkeypatch) -> Iterator[_FakeBackend]:
    backend = _FakeBackend()

    class Cartesia:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.tts = types.SimpleNamespace(websocket_connect=self._connect)

        def close(self) -> None:
            backend.client_closed = True

        @staticmethod
        def _connect(**kwargs) -> _FakeManager:
            backend.connect_kwargs.append(kwargs)
            return _FakeManager(backend)

    package = types.ModuleType("cartesia")
    package.Cartesia = Cartesia  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cartesia", package)
    # The worker and reader poll on this interval: tests wait on the module's
    # logic, not on wall-clock ticks.
    monkeypatch.setattr(cartesia_module, "_POLL_SECONDS", 0.005)
    yield backend
    # Held sockets outlive the utterances: close them so no reader thread
    # polls past its test.
    for ws in backend.sockets:
        ws.closed.set()


def _run(coro):
    return asyncio.run(coro)


def _source(*pieces: str):
    async def gen():
        for piece in pieces:
            yield piece

    return gen()


def _say(module: CartesiaModule, text: str, callback=lambda _b: None) -> None:
    _run(module.synthesize_text(text, TTSOptions(), callback))


def _stream(module: CartesiaModule, *pieces: str, callback=lambda _b: None) -> None:
    _run(module.synthesize_text_stream(_source(*pieces), TTSOptions(), callback))


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cartesia", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[cartesia\]"):
        CartesiaModule(_BASE)


# --- config ------------------------------------------------------------------


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_CARTESIA_KEY", "from-env")
    CartesiaModule(
        {"type": "cartesia", "api_key_env": "MY_CARTESIA_KEY", "voice_id": "v"}
    )
    assert fake.client_kwargs == {"api_key": "from-env"}


@pytest.mark.parametrize(
    ("override", "field"),
    [
        # Checked by the module itself: every shape stays.
        ({"voice_id": None}, "voice_id"),
        ({"voice_id": ""}, "voice_id"),
        ({"sample_rate": 32000}, "sample_rate"),
        ({"sample_rate": 24000.0}, "sample_rate"),
        ({"sample_rate": "24000"}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"max_buffer_delay_ms": 6000}, "max_buffer_delay_ms"),
        ({"max_buffer_delay_ms": 1.5}, "max_buffer_delay_ms"),
        ({"max_buffer_delay_ms": True}, "max_buffer_delay_ms"),
        # Helper-validated fields: one row per field and per bound.
        ({"model": ""}, "model"),
        ({"language": ""}, "language"),
        ({"speed": 0.5}, "speed"),
        ({"speed": 1.6}, "speed"),
        ({"volume": 0.4}, "volume"),
        ({"emotion": 3}, "emotion"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        CartesiaModule({**_BASE, **override})


def test_defaults_reach_the_context(fake):
    module = CartesiaModule(_BASE)
    assert module.sample_rate == 24000
    _say(module, "Hi.")
    assert fake.ws.contexts[0].kwargs == {
        "model_id": "sonic-3.6",
        "voice": "voice-1",
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 24000,
        },
    }


def test_custom_values_reach_the_context(fake):
    module = CartesiaModule(
        {
            **_BASE,
            "model": "sonic-3",
            "sample_rate": 16000,
            "language": "fr",
            "speed": 1,
            "emotion": "calm",
            "max_buffer_delay_ms": 200,
        }
    )
    assert module.sample_rate == 16000
    _say(module, "Salut.")
    assert fake.ws.contexts[0].kwargs == {
        "model_id": "sonic-3",
        "voice": "voice-1",
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 16000,
        },
        "language": "fr",
        "generation_config": {"speed": 1.0, "emotion": "calm"},
        "max_buffer_delay_ms": 200,
    }


# --- synthesize_text ---------------------------------------------------------


def test_text_is_pushed_then_ended_and_audio_reaches_the_callback(fake):
    fake.audio_per_push = None

    def on_push(ctx, _text):
        # Events the module ignores, then audio.
        fake.ws.server_send(_event("flush_done", ctx.context_id))
        fake.ws.server_send(_event("timestamps", ctx.context_id))
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))

    fake.on_push = on_push
    received: list[bytes] = []
    _say(CartesiaModule(_BASE), "Hello world.", received.append)

    (ctx,) = fake.ws.contexts
    # Word-boundary grouping applies to a complete text too.
    assert ctx.pushed == ["Hello ", "world."]
    assert ctx.ended
    assert received == [_AUDIO, _AUDIO]
    # Acknowledged: nothing to cancel, and the socket stays for the next one.
    assert not ctx.cancelled
    assert not fake.ws.closed.is_set()


def test_odd_byte_is_carried_and_audio_is_fed_in_100ms_slices(fake):
    # At 8 kHz a 100 ms slice is 1600 bytes. The first byte waits for the next
    # chunk (one feeder spans the utterance), whole samples go out in slices
    # of the declared rate, and the odd byte left joins the last chunk.
    fake.audio_per_push = [b"\x01", b"\x02" * 3200, b"\x03"]
    received: list[bytes] = []
    _say(CartesiaModule({**_BASE, "sample_rate": 8000}), "Hi.", received.append)
    assert received == [b"\x01" + b"\x02" * 1599, b"\x02" * 1600, b"\x02\x03"]


# --- errors ------------------------------------------------------------------


def test_error_event_is_tts_error(fake):
    def on_push(ctx, _text):
        fake.ws.server_send(
            _event(
                "error",
                ctx.context_id,
                title="Model not found",
                message="The requested model was not found.",
                status_code=404,
            )
        )

    fake.on_push = on_push
    module = CartesiaModule(_BASE)
    with pytest.raises(TTSError, match=r"Model not found.*status 404"):
        _say(module, "Hi.")
    # An `error` leaves the socket usable: the next utterance reuses it.
    fake.on_push = None
    _say(module, "Again.")
    assert fake.connects == 1
    assert not fake.ws.closed.is_set()


def test_connection_closing_before_done_is_tts_error_then_reconnects(fake):
    fake.on_end = lambda _ctx: fake.ws.server_send(ConnectionError("closed 1011"))
    module = CartesiaModule(_BASE)
    with pytest.raises(TTSError, match="closed 1011"):
        _say(module, "Hi.")
    fake.on_end = None
    _say(module, "Again.")
    assert fake.connects == 2


def test_send_error_is_tts_error(fake):
    fake.raise_on_send = OSError("broken pipe")
    with pytest.raises(TTSError, match="broken pipe"):
        _say(CartesiaModule(_BASE), "Hi.")


def test_callback_error_propagates_unchanged_and_keeps_the_socket(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    module = CartesiaModule(_BASE)
    with pytest.raises(ValueError, match="playback failed"):
        _say(module, "Hi.", bad_callback)
    _say(module, "Again.")
    assert fake.connects == 1
    assert not fake.ws.closed.is_set()


async def test_cancel_mid_audio_stops_callbacks_before_raising(fake):
    fake.endless_audio = True
    first = threading.Event()

    task = asyncio.create_task(
        CartesiaModule(_BASE).synthesize_text(
            "Hi.", TTSOptions(), lambda _b: first.set()
        )
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    # The server is told to stop; the socket is kept.
    assert all(ctx.cancelled for ctx in fake.ws.contexts)
    assert not fake.ws.closed.is_set()


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_pushes_word_groups_then_ends(fake):
    _stream(
        CartesiaModule(_BASE), " ", "Hel", "lo wor", "ld. How", " ", "  ", " are you?"
    )
    (ctx,) = fake.ws.contexts
    # Grouped at word boundaries; the blank pieces are never pushed (the API
    # rejects an empty transcript).
    assert ctx.pushed == ["Hello ", "world. ", "How ", " are ", "you?"]
    assert ctx.ended


def test_empty_text_stream_sends_nothing(fake):
    received: list[bytes] = []
    _stream(CartesiaModule(_BASE), "", "  ", callback=received.append)
    # The API rejects a context ended with no text: nothing is opened at all.
    assert fake.ws.contexts == []
    assert received == []
    assert fake.connects == 1
    assert not fake.ws.closed.is_set()


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
        CartesiaModule(_BASE).synthesize_text_stream(source(), TTSOptions(), callback),
        timeout=5,
    )
    # One context: an active utterance is not rotated.
    (ctx,) = fake.ws.contexts
    assert ctx.pushed == ["Hello world. ", "Second ", "one."]


async def test_text_stream_source_error_propagates_unchanged(fake):
    async def source():
        yield "Hello "
        raise RuntimeError("LLM failed")

    with pytest.raises(RuntimeError, match="LLM failed"):
        await CartesiaModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: None
        )
    assert not fake.ws.closed.is_set()
    # The source may fail before "Hello " is pushed; either way nothing is
    # ended and whatever was opened is cancelled.
    assert not any(ctx.ended for ctx in fake.ws.contexts)
    assert all(ctx.cancelled for ctx in fake.ws.contexts)


async def test_text_stream_cancel_while_the_server_is_silent_stops_promptly(fake):
    first = threading.Event()

    async def source():
        yield "Hi there. "  # one audio chunk, then the source stays open
        await asyncio.Event().wait()

    task = asyncio.create_task(
        CartesiaModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: first.set()
        )
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert not fake.ws.closed.is_set()
    (ctx,) = fake.ws.contexts
    assert not ctx.ended
    assert ctx.cancelled


# --- context expiry and rotation ---------------------------------------------


@pytest.fixture
def quick_rotation(monkeypatch) -> dict:
    """Shrink the idle threshold (4 s live) to 20 ms: with no buffer delay it
    is the larger of the floor and the margin. The pauses below (`_PAUSE`)
    are a few times longer, so the worker rotates within them."""
    monkeypatch.setattr(cartesia_module, "_ROTATE_MIN", 0.02)
    monkeypatch.setattr(cartesia_module, "_EXPIRY_MARGIN", 0.02)
    return {**_BASE, "max_buffer_delay_ms": 0}


_PAUSE = 0.08  # an idle pause: past `quick_rotation`'s threshold


async def _stream_with_pause(
    module: CartesiaModule, pause: float, resumed: threading.Event | None = None
) -> list[bytes]:
    """Stream two sentences with a pause after the first one's audio;
    `resumed` is set once the source yields the second."""
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()
    received: list[bytes] = []

    def callback(chunk: bytes) -> None:
        received.append(chunk)
        loop.call_soon_threadsafe(got_audio.set)

    async def source():
        yield "Hello there. "
        await got_audio.wait()
        await asyncio.sleep(pause)
        if resumed is not None:
            resumed.set()
        yield "After the pause."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    return received


async def test_idle_pause_moves_the_rest_to_a_fresh_context(fake, quick_rotation):
    module = CartesiaModule(quick_rotation)

    received = await _stream_with_pause(module, pause=_PAUSE)

    first, second = fake.ws.contexts
    assert first.pushed == ["Hello there. "]
    assert first.ended  # idle: ended by the worker, acknowledged with `done`
    assert second.pushed == ["After the ", "pause."]
    assert second.ended
    assert second.kwargs == first.kwargs
    assert len(received) == 3
    # Both acknowledged: nothing left to cancel.
    assert not first.cancelled and not second.cancelled


async def test_early_done_forces_rotation_and_late_events_are_ignored(fake):
    def on_push(ctx, _text):
        if len(fake.ws.contexts) == 1:
            # The server expires the first context, then complains about it.
            # Both come before its audio: the inbox is FIFO, so the `done` is
            # in hand by the time the source resumes, with no pause needed.
            fake.ws.server_send(_event("done", ctx.context_id))
            fake.ws.server_send(
                _event(
                    "error",
                    ctx.context_id,
                    title="Context expired",
                    message="gone",
                    status_code=400,
                )
            )
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))

    fake.on_push = on_push
    received = await _stream_with_pause(CartesiaModule(_BASE), pause=0)

    first, second = fake.ws.contexts
    assert first.pushed == ["Hello there. "]
    assert not first.ended
    assert second.pushed == ["After the ", "pause."]
    assert second.ended
    assert len(received) == 3


async def test_stream_ending_on_an_idle_context_waits_for_its_done(
    fake, quick_rotation
):
    module = CartesiaModule(quick_rotation)
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello there. "
        await got_audio.wait()
        await asyncio.sleep(_PAUSE)  # the stream ends after an idle pause

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(got_audio.set)

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    (ctx,) = fake.ws.contexts
    assert ctx.ended  # by the worker, during the pause
    assert not ctx.cancelled


async def test_next_context_waits_for_the_idle_ones_done_so_audio_keeps_order(
    fake, quick_rotation
):
    # The idle context's last audio is late: its `done` (and that audio) only
    # come after the text has resumed. The resumed text must not be pushed to
    # a new context before then, or the two would interleave.
    late = b"\x09\x00"
    acknowledged = threading.Event()
    resumed = threading.Event()

    def on_end(ctx):
        if len(fake.ws.contexts) > 1:
            fake.ws.server_send(_event("done", ctx.context_id))
            return

        def answer():
            resumed.wait(5)
            time.sleep(0.02)  # room for a wrong push to the next context
            fake.ws.server_send(_event("chunk", ctx.context_id, audio=late))
            fake.ws.server_send(_event("done", ctx.context_id))
            acknowledged.set()

        threading.Thread(target=answer, daemon=True).start()

    pushed_after_ack: list[bool] = []

    def on_push(ctx, _text):
        if len(fake.ws.contexts) > 1:
            pushed_after_ack.append(acknowledged.is_set())
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))

    fake.on_end = on_end
    fake.on_push = on_push
    received = await _stream_with_pause(
        CartesiaModule(quick_rotation), pause=_PAUSE, resumed=resumed
    )

    first, second = fake.ws.contexts
    assert first.ended and second.ended
    assert pushed_after_ack and all(pushed_after_ack)
    assert received == [_AUDIO, late, _AUDIO, _AUDIO]


# --- contexts as the unit of flow control ------------------------------------


def test_a_context_takes_one_segment_and_the_next_waits_for_its_done(fake, monkeypatch):
    monkeypatch.setattr(cartesia_module, "_SEGMENT_SOFT", 20)
    monkeypatch.setattr(cartesia_module, "_SEGMENT_HARD", 30)
    acknowledged: list[str] = []
    opened_after: list[bool] = []

    def on_end(ctx):
        acknowledged.append(ctx.context_id)
        fake.ws.server_send(_event("done", ctx.context_id))

    def on_push(ctx, _text):
        if not ctx.pushed[:-1]:  # the context's first push
            previous = fake.ws.contexts[-2:-1]
            opened_after.append(not previous or previous[0].context_id in acknowledged)
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))

    fake.on_end = on_end
    fake.on_push = on_push
    text = "One two three. Four five six. Seven eight nine ten eleven twelve. "
    received: list[bytes] = []
    _say(CartesiaModule(_BASE), text, received.append)

    contexts = fake.ws.contexts
    assert len(contexts) >= 3
    assert all(ctx.ended for ctx in contexts)
    assert all(len("".join(ctx.pushed)) <= 30 for ctx in contexts)
    assert "".join(t for ctx in contexts for t in ctx.pushed) == text
    assert all(opened_after)
    assert len(received) == sum(len(ctx.pushed) for ctx in contexts)


async def test_sender_waits_for_playback_before_pushing_more_text(fake, monkeypatch):
    # The bound is one fake chunk (4 bytes): with the callback blocked on the
    # first chunk, the sender pushes text until the audio read fills the bound
    # (plus whatever it sent before the reader caught up), then stops; once
    # playback resumes, the rest goes through.
    monkeypatch.setattr(cartesia_module, "_MAX_UNREAD_SECONDS", 4 / (2 * 24000))
    release = threading.Event()
    first = threading.Event()

    def callback(_chunk: bytes) -> None:
        first.set()
        release.wait(5)

    text = "Word " * 600
    slices = len(list(slice_text(text, 50)))
    task = asyncio.create_task(
        CartesiaModule(_BASE).synthesize_text(text, TTSOptions(), callback)
    )
    await asyncio.to_thread(first.wait, 5)
    await asyncio.sleep(0.05)  # let the sender run as far as the gate allows
    held = sum(len(ctx.pushed) for ctx in fake.ws.contexts)
    assert 1 <= held < slices

    release.set()
    await asyncio.wait_for(task, timeout=5)
    # A complete text goes out in slices of at most 50 characters.
    pushed = [t for ctx in fake.ws.contexts for t in ctx.pushed]
    assert len(pushed) == slices
    assert all(len(t) <= 50 for t in pushed)
    assert "".join(pushed) == text


def test_ended_context_with_a_silent_server_is_tts_error(fake):
    fake.on_end = lambda _ctx: None  # the server never answers `done`
    with pytest.raises(TTSError, match="no answer within"):
        _say(CartesiaModule({**_BASE, "timeout": 0.05}), "Hi.")


# --- the held socket ---------------------------------------------------------


def _wait_dead(module: CartesiaModule) -> None:
    """Wait until the module's reader has seen its socket end."""
    socket = module._socket
    assert socket is not None
    assert socket.dead.wait(1)


def test_construction_connects_once_with_open_timeout(fake):
    CartesiaModule(_BASE)
    # The handshake is bounded by `timeout` on the socket side too.
    assert fake.connect_kwargs == [
        {"websocket_connection_options": {"open_timeout": 30.0}}
    ]
    assert fake.ws.contexts == []


def test_rejected_key_at_construction_is_config_error(fake):
    fake.raise_on_connect = InvalidStatus(types.SimpleNamespace(status_code=401))  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="rejected the key.*401"):
        CartesiaModule(_BASE)
    assert fake.client_closed


def test_other_handshake_failure_at_construction_is_tts_error(fake):
    fake.raise_on_connect = OSError("network down")
    with pytest.raises(TTSError, match="network down"):
        CartesiaModule(_BASE)
    assert fake.client_closed


def test_two_utterances_share_one_socket(fake):
    module = CartesiaModule(_BASE)
    _say(module, "One.")
    _say(module, "Two.")
    assert fake.connects == 1
    first, second = fake.ws.contexts
    assert first.context_id != second.context_id
    assert not fake.ws.closed.is_set()


def test_server_close_between_utterances_reconnects(fake):
    module = CartesiaModule(_BASE)
    _say(module, "One.")
    held = fake.ws
    held.server_close()
    _wait_dead(module)
    received: list[bytes] = []
    _say(module, "Two.", received.append)
    assert fake.connects == 2
    assert held.contexts[-1].pushed == ["One."]
    assert fake.ws is not held and fake.ws.contexts[0].pushed == ["Two."]
    assert received == [_AUDIO]


def test_reconnect_failure_fails_that_utterance_only(fake):
    module = CartesiaModule(_BASE)
    fake.ws.server_close()
    _wait_dead(module)
    fake.raise_on_connect = OSError("HTTP 503")
    with pytest.raises(TTSError, match="HTTP 503"):
        _say(module, "Hi.")
    fake.raise_on_connect = None
    _say(module, "Hi.")
    assert fake.connects == 3
    assert fake.ws.contexts[0].pushed == ["Hi."]


def test_idle_socket_replaced_before_the_first_context(fake):
    module = CartesiaModule(_BASE)
    held = fake.ws
    socket = module._socket
    assert socket is not None
    socket.last_mono -= 241  # idle past _IDLE_REPLACE (the server closes at 300 s)
    socket.last_wall -= 241
    _say(module, "Hi.")
    assert fake.connects == 2
    assert held.contexts == []  # replaced first, never inside the utterance
    assert held.closed.wait(1)
    assert fake.ws.contexts[0].pushed == ["Hi."]


@pytest.mark.parametrize(("slept", "replaced"), [(21, True), (19, False)])
def test_socket_replaced_after_a_host_sleep(fake, slept, replaced):
    # The monotonic clock stops while the host sleeps; the wall clock does not.
    module = CartesiaModule(_BASE)
    socket = module._socket
    assert socket is not None
    socket.last_wall -= slept
    _say(module, "Hi.")
    assert fake.connects == (2 if replaced else 1)


def test_blank_say_touches_nothing(fake):
    module = CartesiaModule(_BASE)
    socket = module._socket
    assert socket is not None
    socket.last_mono -= 241  # would be replaced by a real utterance
    _say(module, "  ")
    assert fake.connects == 1
    assert fake.ws.contexts == []


def test_foreign_events_are_dropped(fake):
    def on_push(ctx, _text):
        # The tail of another context, before and after this one's audio.
        fake.ws.server_send(_event("chunk", "foreign", audio=b"\x07\x00"))
        fake.ws.server_send(_event("error", "foreign", title="x", message="y"))
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))
        fake.ws.server_send(_event("done", "foreign"))

    fake.on_push = on_push
    received: list[bytes] = []
    _say(CartesiaModule(_BASE), "Hello world.", received.append)
    assert received == [_AUDIO, _AUDIO]


async def test_cancel_keeps_the_socket_and_cancels_only_unacknowledged_contexts(
    fake, quick_rotation
):
    # The first context is idle-ended and acknowledged, the second is
    # generating endlessly when the cancel comes.
    module = CartesiaModule(quick_rotation)
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()
    second_audio = threading.Event()

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(got_audio.set)
        if len(fake.ws.contexts) > 1:
            second_audio.set()

    async def source():
        yield "Hello there. "
        await got_audio.wait()
        await asyncio.sleep(_PAUSE)
        fake.endless_audio = True
        yield "After the pause. "
        await asyncio.Event().wait()

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await asyncio.to_thread(second_audio.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    first, second = fake.ws.contexts
    assert first.ended and not first.cancelled
    assert second.cancelled
    fake.endless_audio = False
    await module.synthesize_text("Next.", TTSOptions(), lambda _b: None)
    assert fake.connects == 1


async def test_cancelled_tail_does_not_reach_the_next_utterance(fake):
    tail = b"\x07\x00"
    fake.endless_audio = True
    first = threading.Event()
    module = CartesiaModule(_BASE)
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: first.set())
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    cancelled = fake.ws.contexts[0]
    fake.endless_audio = False
    fake.on_cancel = lambda _ctx: None

    def on_push(ctx, _text):
        # The cancelled context's late chunk and `done` arrive mid-utterance.
        fake.ws.server_send(_event("chunk", cancelled.context_id, audio=tail))
        fake.ws.server_send(_event("done", cancelled.context_id))
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))

    fake.on_push = on_push
    received: list[bytes] = []
    await module.synthesize_text("Next.", TTSOptions(), received.append)
    assert received == [_AUDIO]


async def test_late_sender_never_pushes_into_the_next_utterance(fake, monkeypatch):
    # The sender is held at the admission gate (bound: one chunk) when the
    # utterance is cancelled; once playback is released it must push nothing.
    monkeypatch.setattr(cartesia_module, "_MAX_UNREAD_SECONDS", 4 / (2 * 24000))
    release = threading.Event()
    first = threading.Event()

    def callback(_chunk: bytes) -> None:
        first.set()
        release.wait(5)

    module = CartesiaModule(_BASE)
    task = asyncio.create_task(
        module.synthesize_text("Word " * 600, TTSOptions(), callback)
    )
    await asyncio.to_thread(first.wait, 5)
    await asyncio.sleep(0.02)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    pushed = sum(len(ctx.pushed) for ctx in fake.ws.contexts)
    await module.synthesize_text("Next.", TTSOptions(), lambda _b: None)
    await asyncio.sleep(0.02)
    assert sum(len(ctx.pushed) for ctx in fake.ws.contexts[:-1]) == pushed
    assert fake.ws.contexts[-1].pushed == ["Next."]


def test_stall_drops_the_socket(fake):
    module = CartesiaModule({**_BASE, "timeout": 0.05})
    fake.on_end = lambda _ctx: None  # the server never answers `done`
    with pytest.raises(TTSError, match="no answer within"):
        _say(module, "Hi.")
    assert fake.ws.closed.wait(1)
    fake.on_end = None
    _say(module, "Again.")
    assert fake.connects == 2


def test_close_closes_the_socket_and_the_client(fake):
    module = CartesiaModule(_BASE)
    module.close()
    assert fake.ws.closed.wait(1)
    assert fake.client_closed


def test_close_with_a_dead_socket_closes_only_the_client(fake):
    module = CartesiaModule(_BASE)
    fake.ws.server_close()
    _wait_dead(module)
    module.close()
    assert fake.client_closed
    assert fake.connects == 1


# --- connection setup --------------------------------------------------------


async def test_cancel_during_a_reconnect_handshake_surfaces_promptly(fake):
    module = CartesiaModule(_BASE)
    fake.ws.server_close()
    _wait_dead(module)
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
    def opened_then_closed() -> bool:
        deadline = time.perf_counter() + 2
        while len(fake.sockets) < 2 and time.perf_counter() < deadline:
            time.sleep(0.01)
        return len(fake.sockets) == 2 and fake.ws.closed.wait(1)

    assert await asyncio.to_thread(opened_then_closed)
    assert fake.ws.contexts == []
    fake.connect_gate = None
    await module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    assert fake.connects == 3


def test_handshake_slower_than_timeout_at_construction_is_tts_error(fake):
    fake.connect_gate = threading.Event()
    with pytest.raises(TTSError, match="not open within"):
        CartesiaModule({**_BASE, "timeout": 0.05})
    fake.connect_gate.set()


def test_handshake_slower_than_timeout_on_a_reconnect_is_tts_error(fake):
    module = CartesiaModule({**_BASE, "timeout": 0.05})
    fake.ws.server_close()
    _wait_dead(module)
    fake.connect_gate = threading.Event()
    with pytest.raises(TTSError, match="not open within"):
        _say(module, "Hi.")
    fake.connect_gate.set()


def test_no_reader_outlives_a_closed_module(fake):
    before = set(threading.enumerate())
    module = CartesiaModule(_BASE)
    _say(module, "Hi.")
    readers = [
        t for t in set(threading.enumerate()) - before if t.name == "cartesia-reader"
    ]
    assert len(readers) == 1  # one per socket, not one per utterance
    module.close()
    readers[0].join(1)
    assert not readers[0].is_alive()
