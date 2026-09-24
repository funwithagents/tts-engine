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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

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


@pytest.fixture
def fake_gradium(monkeypatch):
    """Install a fake `gradium` package; returns a recorder of what reached it."""
    recorder = MagicMock()
    recorder.messages = []
    recorder.ready = {"type": "ready", "sample_rate": 48000, "request_id": "r1"}
    recorder.tts = None

    class FakeClient:
        def __init__(self, **kwargs):
            recorder.client_kwargs = kwargs

        def tts_realtime(self, **kwargs):
            recorder.setup_kwargs = kwargs
            recorder.tts = FakeTts(iter(recorder.messages), recorder.ready)
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


def test_missing_api_key_raises(fake_gradium):
    with pytest.raises(ConfigError, match="api_key"):
        GradiumModule({"type": "gradium", "voice_id": "v"})


def test_empty_api_key_raises(fake_gradium):
    with pytest.raises(ConfigError, match="api_key"):
        GradiumModule({"type": "gradium", "api_key": "", "voice_id": "v"})


def test_missing_voice_id_raises(fake_gradium):
    with pytest.raises(ConfigError, match="voice_id"):
        GradiumModule({"type": "gradium", "api_key": "k"})


def test_api_key_from_env(fake_gradium, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    GradiumModule({"type": "gradium", "api_key_env": "MY_TTS_KEY", "voice_id": "v"})
    assert fake_gradium.client_kwargs == {"api_key": "env-secret"}


def test_literal_api_key_takes_precedence_over_env(fake_gradium, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    GradiumModule({**VALID_CONFIG, "api_key_env": "MY_TTS_KEY"})
    assert fake_gradium.client_kwargs == {"api_key": "test-api-key"}


def test_api_key_env_pointing_to_unset_var_raises(fake_gradium, monkeypatch):
    monkeypatch.delenv("MY_TTS_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_TTS_KEY"):
        GradiumModule({"type": "gradium", "api_key_env": "MY_TTS_KEY", "voice_id": "v"})


def test_base_url_reaches_the_client_only_when_set(fake_gradium):
    GradiumModule(VALID_CONFIG)
    assert "base_url" not in fake_gradium.client_kwargs
    GradiumModule({**VALID_CONFIG, "base_url": "https://eu.example/api/"})
    assert fake_gradium.client_kwargs["base_url"] == "https://eu.example/api/"


@pytest.mark.parametrize(
    "override, field",
    [
        ({"api_key": 123}, "api_key"),
        ({"api_key_env": 5}, "api_key_env"),
        ({"voice_id": 42}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 7}, "model"),
        ({"sample_rate": 22050}, "sample_rate"),
        ({"sample_rate": "48000"}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"temp": 1.5}, "temp"),
        ({"temp": True}, "temp"),
        ({"cfg_coef": 0.5}, "cfg_coef"),
        ({"cfg_coef": "x"}, "cfg_coef"),
        ({"padding_bonus": -5}, "padding_bonus"),
        ({"rewrite_rules": ""}, "rewrite_rules"),
        ({"pronunciation_id": 3}, "pronunciation_id"),
        ({"base_url": ""}, "base_url"),
    ],
)
def test_invalid_field_raises_config_error(fake_gradium, override, field):
    with pytest.raises(ConfigError, match=field):
        GradiumModule({**VALID_CONFIG, **override})


def test_sample_rate_reflects_config(fake_gradium):
    assert GradiumModule(VALID_CONFIG).sample_rate == 48000
    assert GradiumModule({**VALID_CONFIG, "sample_rate": 24000}).sample_rate == 24000


# --- streaming ---------------------------------------------------------------


async def test_defaults_reach_the_provider_client(fake_gradium):
    module = GradiumModule(VALID_CONFIG)
    await module.stream("hello", TTSOptions(), MagicMock())

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
    await GradiumModule(config).stream("hello", TTSOptions(), MagicMock())

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
    await GradiumModule({**VALID_CONFIG, "temp": 0.2}).stream(
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
    asyncio.run(GradiumModule(VALID_CONFIG).stream("hello", TTSOptions(), callback))

    assert [c.args[0] for c in callback.call_args_list] == [
        b"\x01\x00\x02\x00",
        b"\x03\x00",
    ]


def test_ready_sample_rate_mismatch_raises_tts_error(fake_gradium):
    fake_gradium.ready["sample_rate"] = 24000
    fake_gradium.messages = [_audio(b"\x01\x00")]
    callback = MagicMock()
    with pytest.raises(TTSError, match="24000"):
        asyncio.run(GradiumModule(VALID_CONFIG).stream("hello", TTSOptions(), callback))
    callback.assert_not_called()
    assert fake_gradium.tts.closed is True


def test_stream_wraps_sdk_exception_in_tts_error(fake_gradium):
    fake_gradium.messages = [_audio(b"\x01\x00"), RuntimeError("Error from server")]
    callback = MagicMock()
    with pytest.raises(TTSError, match="Error from server"):
        asyncio.run(GradiumModule(VALID_CONFIG).stream("hello", TTSOptions(), callback))
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
        asyncio.run(module.stream("hello", TTSOptions(), MagicMock()))


def test_stream_does_not_mask_callback_errors(fake_gradium):
    fake_gradium.messages = [_audio(b"\x01\x00")]

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(
            GradiumModule(VALID_CONFIG).stream("hello", TTSOptions(), bad_callback)
        )
    assert fake_gradium.tts.closed is True


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
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    module = GradiumModule(VALID_CONFIG)
    task = asyncio.create_task(module.stream("hello", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)  # ~10 chunk periods: a leaked worker would show
    assert count == settled
    assert fake_gradium.tts.closed is True
