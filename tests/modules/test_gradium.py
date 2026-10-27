"""Unit tests for GradiumModule.

The `gradium` SDK is faked: a stub module is injected into `sys.modules` whose
`GradiumClient.tts_realtime` returns a scripted async context manager /
iterator, so no socket is ever opened and the tests run without the extra.
Provider configuration is observed through the arguments sent to the client
(testing.md), never through private attributes.
"""

import asyncio
import sys
import time
import types
from unittest.mock import MagicMock

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.gradium import GradiumModule
from tts_engine.modules.gradium import module as gradium_module

VALID_CONFIG = {
    "type": "gradium",
    "api_key": "test-api-key",
    "voice_id": "test-voice-id",
}


def _audio(data: bytes) -> dict:
    return {"type": "audio", "audio": data, "start_s": 0.0, "stop_s": 0.08}


class FakeTts:
    """Scripted stand-in for `gradium.stream.Tts`."""

    def __init__(self, messages, ready: dict | None):
        self._messages = messages
        self.ready = ready
        self.sent: list[str] = []
        self.eos = False
        self.closed = False
        self.opened = False  # resources taken by __aenter__, even if it fails
        self.fail_enter: Exception | None = None  # raised after `opened`
        self.hang_enter = False  # `ready` never comes
        self.fail_exit: Exception | None = None

    async def __aenter__(self):
        self.opened = True
        if self.fail_enter is not None:
            raise self.fail_enter
        if self.hang_enter:
            await asyncio.Event().wait()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        if self.fail_exit is not None:
            raise self.fail_exit

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send_eos(self) -> None:
        self.eos = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None
        if isinstance(item, Exception):
            raise item
        return item


class ReactiveFakeTts(FakeTts):
    """A fake that answers each `send_text` with one audio message (the text's
    bytes) and ends the stream after `send_eos`, the way the server behaves
    over time, so text-stream tests can observe audio arriving between pieces."""

    def __init__(self, messages, ready):
        super().__init__(messages, ready)
        self._queue: asyncio.Queue | None = None

    def _q(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()  # bound to the module's private loop
        return self._queue

    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        self._q().put_nowait(_audio(text.encode()))

    async def send_eos(self) -> None:
        await super().send_eos()
        self._q().put_nowait(None)

    async def __anext__(self):
        item = await self._q().get()
        if item is None:
            raise StopAsyncIteration
        return item


class SilentFakeTts(FakeTts):
    """A fake that sends its scripted messages, then stays silent — awaiting
    forever instead of ending the stream — like a server still synthesizing."""

    async def __anext__(self):
        try:
            return await super().__anext__()
        except StopAsyncIteration:
            await asyncio.Event().wait()
            raise


@pytest.fixture
def fake_gradium(monkeypatch):
    """Install a fake `gradium` package; returns a recorder of what reached it.

    The receive/setup poll is patched down so cancel and timeout tests wait on
    the module's logic, not on 100 ms wall-clock ticks (testing.md)."""
    monkeypatch.setattr(gradium_module, "_RECV_POLL_SECONDS", 0.005)
    recorder = MagicMock()
    recorder.messages = []
    recorder.ready = {"type": "ready", "sample_rate": 48000, "request_id": "r1"}
    recorder.tts = None
    recorder.reactive = False
    recorder.silent = False

    class FakeClient:
        def __init__(self, **kwargs):
            recorder.client_kwargs = kwargs

        def tts_realtime(self, **kwargs):
            recorder.setup_kwargs = kwargs
            cls = FakeTts
            if recorder.reactive:
                cls = ReactiveFakeTts
            elif recorder.silent:
                cls = SilentFakeTts
            recorder.tts = cls(iter(recorder.messages), recorder.ready)
            return recorder.tts

    module = types.ModuleType("gradium")
    module.GradiumClient = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gradium", module)
    return recorder


# --- construction / config -------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "gradium", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[gradium\]"):
        GradiumModule(VALID_CONFIG)


def test_missing_voice_id_raises(fake_gradium):
    with pytest.raises(ConfigError, match="voice_id"):
        GradiumModule({"type": "gradium", "api_key": "k"})


def test_api_key_from_env(fake_gradium, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    GradiumModule({"type": "gradium", "api_key_env": "MY_TTS_KEY", "voice_id": "v"})
    assert fake_gradium.client_kwargs == {"api_key": "env-secret"}


def test_base_url_reaches_the_client_only_when_set(fake_gradium):
    GradiumModule(VALID_CONFIG)
    assert "base_url" not in fake_gradium.client_kwargs
    GradiumModule({**VALID_CONFIG, "base_url": "https://eu.example/api/"})
    assert fake_gradium.client_kwargs["base_url"] == "https://eu.example/api/"


@pytest.mark.parametrize(
    "override, field",
    [
        ({"voice_id": 42}, "voice_id"),
        ({"model": ""}, "model"),
        ({"sample_rate": 22050}, "sample_rate"),
        ({"sample_rate": 48000.0}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"temp": 1.5}, "temp"),
        ({"cfg_coef": 0.5}, "cfg_coef"),
        ({"padding_bonus": -5}, "padding_bonus"),
        ({"rewrite_rules": ""}, "rewrite_rules"),
        ({"pronunciation_id": 3}, "pronunciation_id"),
        ({"base_url": ""}, "base_url"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake_gradium, override, field):
    with pytest.raises(ConfigError, match=field):
        GradiumModule({**VALID_CONFIG, **override})


# --- streaming ---------------------------------------------------------------


async def test_defaults_reach_the_provider_client(fake_gradium):
    module = GradiumModule(VALID_CONFIG)
    await module.synthesize_text("hello", TTSOptions(), MagicMock())

    assert module.sample_rate == 48000
    assert fake_gradium.setup_kwargs == {
        "wait_for_ready_on_start": True,
        "model_name": "default",
        "voice_id": "test-voice-id",
        "output_format": "pcm_48000",
    }
    assert fake_gradium.tts.sent == ["hello"]
    assert fake_gradium.tts.eos is True
    assert fake_gradium.tts.closed is True


async def test_custom_values_reach_the_provider_client(fake_gradium):
    fake_gradium.ready["sample_rate"] = 24000
    config = {
        **VALID_CONFIG,
        "model": "gradium-tts-beta",
        "sample_rate": 24000,
        "temp": 0.3,
        "cfg_coef": 2.5,
        "padding_bonus": -1,
        "rewrite_rules": "none",
        "pronunciation_id": "pron-1",
    }
    module = GradiumModule(config)
    await module.synthesize_text("hello", TTSOptions(), MagicMock())

    assert module.sample_rate == 24000
    kwargs = fake_gradium.setup_kwargs
    assert kwargs["model_name"] == "gradium-tts-beta"
    assert kwargs["output_format"] == "pcm_24000"
    assert kwargs["pronunciation_id"] == "pron-1"
    assert kwargs["json_config"] == {
        "temp": 0.3,
        "cfg_coef": 2.5,
        "padding_bonus": -1.0,
        "rewrite_rules": "none",
    }


async def test_partial_voice_settings_send_only_what_was_set(fake_gradium):
    await GradiumModule({**VALID_CONFIG, "temp": 0.2}).synthesize_text(
        "hi", TTSOptions(), MagicMock()
    )
    assert fake_gradium.setup_kwargs["json_config"] == {"temp": 0.2}


def test_stream_feeds_audio_bytes_and_skips_other_messages(fake_gradium):
    fake_gradium.messages = [
        {"type": "text", "text": "hel", "start_s": 0.0, "stop_s": 0.1},
        _audio(b"\x01\x00\x02\x00"),
        _audio(b"\x03\x00"),
        {"type": "end_of_stream"},
    ]
    callback = MagicMock()
    asyncio.run(
        GradiumModule(VALID_CONFIG).synthesize_text("hello", TTSOptions(), callback)
    )

    assert [c.args[0] for c in callback.call_args_list] == [
        b"\x01\x00\x02\x00",
        b"\x03\x00",
    ]


def test_ready_sample_rate_mismatch_raises_tts_error(fake_gradium):
    fake_gradium.ready["sample_rate"] = 24000
    fake_gradium.messages = [_audio(b"\x01\x00")]
    callback = MagicMock()
    with pytest.raises(TTSError, match="24000"):
        asyncio.run(
            GradiumModule(VALID_CONFIG).synthesize_text("hello", TTSOptions(), callback)
        )
    callback.assert_not_called()
    assert fake_gradium.tts.closed is True


def test_stream_wraps_sdk_exception_in_tts_error(fake_gradium):
    fake_gradium.messages = [_audio(b"\x01\x00"), RuntimeError("Error from server")]
    callback = MagicMock()
    with pytest.raises(TTSError, match="Error from server"):
        asyncio.run(
            GradiumModule(VALID_CONFIG).synthesize_text("hello", TTSOptions(), callback)
        )
    callback.assert_called_once_with(b"\x01\x00")
    assert fake_gradium.tts.closed is True


def test_stream_wraps_connection_failure_in_tts_error(fake_gradium, monkeypatch):
    def failing_tts_realtime(self, **kwargs):
        raise OSError("connection refused")

    module = GradiumModule(VALID_CONFIG)
    monkeypatch.setattr(
        sys.modules["gradium"].GradiumClient,  # type: ignore[attr-defined]
        "tts_realtime",
        failing_tts_realtime,
    )
    with pytest.raises(TTSError, match="connection refused"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))


def _scripted_connection(fake_gradium, **flags):
    """Make the fake client's next `tts_realtime` return a `FakeTts` with the
    given flags set (`fail_enter`, `hang_enter`, `fail_exit`)."""
    original = sys.modules["gradium"].GradiumClient.tts_realtime  # type: ignore[attr-defined]

    def tts_realtime(self, **kwargs):
        tts = original(self, **kwargs)
        for name, value in flags.items():
            setattr(tts, name, value)
        return tts

    sys.modules["gradium"].GradiumClient.tts_realtime = tts_realtime  # type: ignore[attr-defined]


def test_setup_failure_releases_the_partial_connection(fake_gradium):
    # The SDK's `__aenter__` opens a session and socket before it waits for
    # `ready`; when that fails (a rejected setup) it closes nothing of its
    # own, so the module must, while reporting the original failure.
    module = GradiumModule(VALID_CONFIG)
    _scripted_connection(
        fake_gradium, fail_enter=RuntimeError("Expected ready message, got: error")
    )
    with pytest.raises(TTSError, match="Expected ready message"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))
    assert fake_gradium.tts.opened is True
    assert fake_gradium.tts.closed is True


def test_setup_timeout_is_a_tts_error_and_releases_the_connection(fake_gradium):
    module = GradiumModule({**VALID_CONFIG, "timeout": 0.02})
    _scripted_connection(fake_gradium, hang_enter=True)
    with pytest.raises(TTSError, match="not ready within 0.02 s"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))
    assert fake_gradium.tts.closed is True


async def test_cancel_during_setup_stops_promptly_and_releases_the_connection(
    fake_gradium,
):
    # No `ready` yet (a slow or silent server): a cancel must not wait for it.
    module = GradiumModule(VALID_CONFIG)
    _scripted_connection(fake_gradium, hang_enter=True)
    task = asyncio.create_task(
        module.synthesize_text("hello", TTSOptions(), MagicMock())
    )
    while fake_gradium.tts is None or not fake_gradium.tts.opened:
        await asyncio.sleep(0.001)  # until the worker is inside the setup wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake_gradium.tts.closed is True


def test_callback_error_wins_over_a_failing_close(fake_gradium):
    fake_gradium.messages = [_audio(b"\x01\x00")]
    module = GradiumModule(VALID_CONFIG)
    _scripted_connection(fake_gradium, fail_exit=RuntimeError("teardown failed"))

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), bad_callback))
    assert fake_gradium.tts.closed is True


def _signal(loop: asyncio.AbstractEventLoop, event: asyncio.Event):
    """A callback that sets `event` on `loop` from the module's worker thread."""

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(event.set)

    return callback


async def test_cancel_stops_callbacks_before_stream_raises(fake_gradium):
    # An endless stream: without cooperative cancellation the worker thread
    # would keep calling back forever after stream() has raised.
    def endless():
        while True:
            time.sleep(0.005)
            yield _audio(b"\x01\x00")

    fake_gradium.messages = endless()

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()

    module = GradiumModule(VALID_CONFIG)
    task = asyncio.create_task(
        module.synthesize_text("hello", TTSOptions(), _signal(loop, first_chunk))
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake_gradium.tts.closed is True


async def test_cancel_while_the_server_is_silent_stops_promptly(fake_gradium):
    # One audio message, then nothing (the server is still synthesizing, no
    # end-of-stream yet): the receive loop must be polling `stop`, not blocked
    # on the next message, or the cancel hangs for as long as the server does.
    fake_gradium.silent = True
    fake_gradium.messages = [_audio(b"\x01\x02")]
    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()

    module = GradiumModule(VALID_CONFIG)
    task = asyncio.create_task(
        module.synthesize_text("hello", TTSOptions(), _signal(loop, first_chunk))
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake_gradium.tts.closed is True


# --- text stream (sender + receiver on one connection) -----------------------


def _source(*pieces: str):
    async def source():
        for piece in pieces:
            yield piece

    return source()


def test_text_stream_sends_word_groups_then_eos(fake_gradium):
    fake_gradium.reactive = True
    callback = MagicMock()

    asyncio.run(
        GradiumModule(VALID_CONFIG).synthesize_text_stream(
            _source("Hel", "lo wor", "ld, how", " are you"), TTSOptions(), callback
        )
    )

    # Pieces are forwarded up to their last whitespace, so a sub-word token is
    # never sent on its own; the remainder goes before the single end-of-stream.
    assert fake_gradium.tts.sent == ["Hello ", "world, ", "how are ", "you"]
    assert fake_gradium.tts.eos is True
    assert [c.args[0] for c in callback.call_args_list] == [
        b"Hello ",
        b"world, ",
        b"how are ",
        b"you",
    ]
    assert fake_gradium.tts.closed is True


async def test_text_stream_feeds_audio_before_the_source_finishes(fake_gradium):
    fake_gradium.reactive = True
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello "
        await got_audio.wait()
        yield "world."

    await asyncio.wait_for(
        GradiumModule(VALID_CONFIG).synthesize_text_stream(
            source(), TTSOptions(), _signal(loop, got_audio)
        ),
        timeout=5,
    )
    assert fake_gradium.tts.sent == ["Hello ", "world."]
    assert fake_gradium.tts.eos is True


def test_text_stream_sender_failure_raises_tts_error(fake_gradium, monkeypatch):
    fake_gradium.reactive = True

    async def failing_send(self, text):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(ReactiveFakeTts, "send_text", failing_send)

    with pytest.raises(TTSError, match="socket closed"):
        asyncio.run(
            GradiumModule(VALID_CONFIG).synthesize_text_stream(
                _source("Hello "), TTSOptions(), MagicMock()
            )
        )
    assert fake_gradium.tts.closed is True


def test_text_stream_source_error_propagates_unchanged(fake_gradium):
    fake_gradium.reactive = True

    async def source():
        yield "Hello "
        raise RuntimeError("llm failed")

    with pytest.raises(RuntimeError, match="llm failed"):
        asyncio.run(
            GradiumModule(VALID_CONFIG).synthesize_text_stream(
                source(), TTSOptions(), MagicMock()
            )
        )
    # Aborted: the request is dropped without an end-of-stream.
    assert fake_gradium.tts.eos is False
    assert fake_gradium.tts.closed is True


async def test_text_stream_cancel_mid_audio_stops_callbacks_before_raising(
    fake_gradium,
):
    # Endless audio from the server while the sender is still blocked waiting
    # for text: cancel must stop the receiver, release the sender's executor
    # thread, and close the socket, all before CancelledError surfaces.
    def endless():
        while True:
            time.sleep(0.005)
            yield _audio(b"\x01\x00")

    fake_gradium.messages = endless()
    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()

    async def source():
        yield "Hello "
        await asyncio.Event().wait()  # the source stays open

    module = GradiumModule(VALID_CONFIG)
    task = asyncio.create_task(
        module.synthesize_text_stream(
            source(), TTSOptions(), _signal(loop, first_chunk)
        )
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake_gradium.tts.closed is True
    assert fake_gradium.tts.eos is False  # dropped, not finished
