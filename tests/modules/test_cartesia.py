"""Unit tests for CartesiaModule with the cartesia SDK faked.

The fast tier never opens a socket, so `cartesia` is faked via `sys.modules`.
The fake connection answers the way the live API was observed to: a chunk of
audio per push, `done` after `no_more_inputs`, and `TimeoutError` from
`recv_bytes` while the server is silent. Hooks let a test script early `done`s
(context expiry), `error` events and connection failures.
"""

import asyncio
import queue
import sys
import threading
import types

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
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


class _FakeWS:
    """The SDK's `TTSResourceConnection`: contexts plus a polled receive."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self.backend = backend
        self._outbox: queue.Queue = queue.Queue()
        self.contexts: list[_FakeContext] = []
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
        self._outbox.put(item)

    def recv_bytes(self, timeout: float | None = None):
        if self.backend.endless_audio and self.contexts:
            if self.closed.wait(0.001):
                raise ConnectionError("socket closed")
            return _event("chunk", self.contexts[-1].context_id, audio=_AUDIO)
        try:
            item = self._outbox.get(timeout=timeout)
        except queue.Empty:
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

    def __enter__(self) -> _FakeWS:
        if self._backend.raise_on_connect is not None:
            raise self._backend.raise_on_connect
        ws = _FakeWS(self._backend)
        self._backend.sockets.append(ws)
        return ws

    def __exit__(self, *_exc) -> None:
        self._backend.sockets[-1].closed.set()


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.connect_kwargs: list[dict] = []
        self.sockets: list[_FakeWS] = []
        self.audio_per_push: list[bytes] | None = [_AUDIO]
        self.on_push = None  # (ctx, text) -> None, replaces the default answer
        self.on_end = None  # (ctx) -> None, replaces the default `done`
        self.endless_audio = False
        self.raise_on_connect: Exception | None = None
        self.raise_on_send: Exception | None = None

    @property
    def ws(self) -> _FakeWS:
        return self.sockets[-1]


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class Cartesia:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.tts = types.SimpleNamespace(websocket_connect=self._connect)

        @staticmethod
        def _connect(**kwargs) -> _FakeManager:
            backend.connect_kwargs.append(kwargs)
            return _FakeManager(backend)

    package = types.ModuleType("cartesia")
    package.Cartesia = Cartesia  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cartesia", package)
    return backend


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


def test_missing_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        CartesiaModule({"type": "cartesia", "voice_id": "v"})


def test_empty_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        CartesiaModule({**_BASE, "api_key": ""})


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_CARTESIA_KEY", "from-env")
    CartesiaModule(
        {"type": "cartesia", "api_key_env": "MY_CARTESIA_KEY", "voice_id": "v"}
    )
    assert fake.client_kwargs == {"api_key": "from-env"}


def test_literal_api_key_takes_precedence_over_env(fake, monkeypatch):
    monkeypatch.setenv("MY_CARTESIA_KEY", "from-env")
    CartesiaModule({**_BASE, "api_key_env": "MY_CARTESIA_KEY"})
    assert fake.client_kwargs == {"api_key": "k"}


def test_api_key_env_pointing_to_unset_var_raises(fake, monkeypatch):
    monkeypatch.delenv("MY_CARTESIA_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_CARTESIA_KEY"):
        CartesiaModule(
            {"type": "cartesia", "api_key_env": "MY_CARTESIA_KEY", "voice_id": "v"}
        )


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"api_key": 3}, "api_key"),
        ({"api_key": None, "api_key_env": 3}, "api_key_env"),
        ({"voice_id": None}, "voice_id"),
        ({"voice_id": ""}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"sample_rate": 32000}, "sample_rate"),
        ({"sample_rate": "24000"}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"language": ""}, "language"),
        ({"speed": 0.5}, "speed"),
        ({"speed": 1.6}, "speed"),
        ({"speed": True}, "speed"),
        ({"volume": 0.4}, "volume"),
        ({"volume": "1"}, "volume"),
        ({"emotion": 3}, "emotion"),
        ({"max_buffer_delay_ms": 6000}, "max_buffer_delay_ms"),
        ({"max_buffer_delay_ms": 1.5}, "max_buffer_delay_ms"),
        ({"max_buffer_delay_ms": True}, "max_buffer_delay_ms"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        CartesiaModule({**_BASE, **override})


def test_sample_rate_reflects_config(fake):
    assert CartesiaModule(_BASE).sample_rate == 24000
    assert CartesiaModule({**_BASE, "sample_rate": 44100}).sample_rate == 44100


def test_defaults_reach_the_context(fake):
    _say(CartesiaModule(_BASE), "Hi.")
    # No receive flow control, so a close is never stuck behind unread audio.
    assert fake.connect_kwargs == [
        {"websocket_connection_options": {"max_queue": None}}
    ]
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
    # Cancelled before the close, which keeps the close prompt.
    assert ctx.cancelled
    assert fake.ws.closed.wait(1)


def test_odd_byte_is_carried_to_the_next_chunk(fake):
    fake.audio_per_push = [b"\x01", b"\x02\x03", b"\x04"]
    received: list[bytes] = []
    _say(CartesiaModule(_BASE), "Hi.", received.append)
    assert received == [b"\x01\x02", b"\x03\x04"]


# --- errors ------------------------------------------------------------------


def test_connect_error_is_tts_error(fake):
    fake.raise_on_connect = OSError("HTTP 401")
    with pytest.raises(TTSError, match="HTTP 401"):
        _say(CartesiaModule(_BASE), "Hi.")


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
    with pytest.raises(TTSError, match=r"Model not found.*status 404"):
        _say(CartesiaModule(_BASE), "Hi.")
    assert fake.ws.closed.wait(1)


def test_connection_closing_before_done_is_tts_error(fake):
    fake.on_end = lambda _ctx: fake.ws.server_send(ConnectionError("closed 1011"))
    with pytest.raises(TTSError, match="closed 1011"):
        _say(CartesiaModule(_BASE), "Hi.")


def test_send_error_is_tts_error(fake):
    fake.raise_on_send = OSError("broken pipe")
    with pytest.raises(TTSError, match="broken pipe"):
        _say(CartesiaModule(_BASE), "Hi.")


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(CartesiaModule(_BASE), "Hi.", bad_callback)
    assert fake.ws.closed.wait(1)


async def test_cancel_mid_audio_stops_callbacks_before_raising(fake):
    fake.endless_audio = True
    count = 0
    first = threading.Event()

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        first.set()

    task = asyncio.create_task(
        CartesiaModule(_BASE).synthesize_text("Hi.", TTSOptions(), callback)
    )
    await asyncio.to_thread(first.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
    assert await asyncio.to_thread(fake.ws.closed.wait, 1)
    assert all(ctx.cancelled for ctx in fake.ws.contexts)


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_pushes_word_groups_then_ends(fake):
    _stream(CartesiaModule(_BASE), "Hel", "lo wor", "ld. How", " are you?")
    (ctx,) = fake.ws.contexts
    assert ctx.pushed == ["Hello ", "world. ", "How are ", "you?"]
    assert ctx.ended


def test_text_stream_never_pushes_blank_text(fake):
    _stream(CartesiaModule(_BASE), "Hi ", " ", "  ", "there")
    (ctx,) = fake.ws.contexts
    assert all(text.strip() for text in ctx.pushed)
    assert "".join(ctx.pushed).split() == ["Hi", "there"]


def test_empty_text_stream_sends_nothing(fake):
    received: list[bytes] = []
    _stream(CartesiaModule(_BASE), "", "  ", callback=received.append)
    # The API rejects a context ended with no text: nothing is opened at all.
    assert fake.ws.contexts == []
    assert received == []
    assert fake.ws.closed.wait(1)


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
    assert fake.ws.contexts[0].pushed == ["Hello world. ", "Second ", "one."]


async def test_text_stream_source_error_propagates_unchanged(fake):
    async def source():
        yield "Hello "
        raise RuntimeError("LLM failed")

    with pytest.raises(RuntimeError, match="LLM failed"):
        await CartesiaModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: None
        )
    assert await asyncio.to_thread(fake.ws.closed.wait, 1)
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
    assert await asyncio.to_thread(fake.ws.closed.wait, 1)
    (ctx,) = fake.ws.contexts
    assert not ctx.ended
    assert ctx.cancelled


# --- context expiry and rotation ---------------------------------------------


@pytest.fixture
def quick_rotation(monkeypatch) -> dict:
    """Shrink the idle threshold (4 s live) to 50 ms: with no buffer delay it
    is the larger of the floor and the margin."""
    monkeypatch.setattr(cartesia_module, "_ROTATE_MIN", 0.05)
    monkeypatch.setattr(cartesia_module, "_EXPIRY_MARGIN", 0.05)
    return {**_BASE, "max_buffer_delay_ms": 0}


async def _stream_with_pause(module: CartesiaModule, pause: float) -> list[bytes]:
    """Stream two sentences with a pause after the first one's audio."""
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
        yield "After the pause."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    return received


async def test_idle_pause_moves_the_rest_to_a_fresh_context(fake, quick_rotation):
    module = CartesiaModule(quick_rotation)

    received = await _stream_with_pause(module, pause=0.2)

    first, second = fake.ws.contexts
    assert first.pushed == ["Hello there. "]
    assert not first.ended  # idle: it had spoken everything
    assert second.pushed == ["After the ", "pause."]
    assert second.ended
    assert second.kwargs == first.kwargs
    assert len(received) == 3
    assert first.cancelled and second.cancelled


async def test_no_rotation_while_the_utterance_is_active(fake):
    received = await _stream_with_pause(CartesiaModule(_BASE), pause=0.05)
    (ctx,) = fake.ws.contexts
    assert ctx.pushed == ["Hello there. ", "After the ", "pause."]
    assert len(received) == 3


async def test_early_done_forces_rotation_and_late_events_are_ignored(fake):
    def on_push(ctx, _text):
        fake.ws.server_send(_event("chunk", ctx.context_id, audio=_AUDIO))
        if len(fake.ws.contexts) == 1:
            # The server expires the first context, then complains about it.
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

    fake.on_push = on_push
    received = await _stream_with_pause(CartesiaModule(_BASE), pause=0.3)

    first, second = fake.ws.contexts
    assert first.pushed == ["Hello there. "]
    assert not first.ended
    assert second.pushed == ["After the ", "pause."]
    assert second.ended
    assert len(received) == 3


async def test_stream_ending_on_an_idle_context_returns_without_ending_it(
    fake, quick_rotation
):
    module = CartesiaModule(quick_rotation)
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello there. "
        await got_audio.wait()
        await asyncio.sleep(0.2)  # the stream ends after an idle pause

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(got_audio.set)

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    (ctx,) = fake.ws.contexts
    assert not ctx.ended
    assert ctx.cancelled
