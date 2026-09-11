"""Unit tests for ElevenLabsModule."""

import array as _array
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.elevenlabs import ElevenLabsModule

VALID_CONFIG = {
    "type": "elevenlabs",
    "api_key": "test-api-key",
    "voice_id": "test-voice-id",
}


def test_missing_api_key_raises():
    with pytest.raises(ConfigError, match="api_key"):
        ElevenLabsModule({"type": "elevenlabs", "voice_id": "v"})


def test_empty_api_key_raises():
    with pytest.raises(ConfigError, match="api_key"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "", "voice_id": "v"})


def test_missing_voice_id_raises():
    with pytest.raises(ConfigError, match="voice_id"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "k"})


def test_empty_voice_id_raises():
    with pytest.raises(ConfigError, match="voice_id"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "k", "voice_id": ""})


@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_api_key_from_env(mock_elevenlabs_cls, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    config = {"type": "elevenlabs", "api_key_env": "MY_TTS_KEY", "voice_id": "v"}
    ElevenLabsModule(config)
    mock_elevenlabs_cls.assert_called_once_with(api_key="env-secret")


@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_literal_api_key_takes_precedence_over_env(mock_elevenlabs_cls, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    config = {
        "type": "elevenlabs",
        "api_key": "literal-key",
        "api_key_env": "MY_TTS_KEY",
        "voice_id": "v",
    }
    ElevenLabsModule(config)
    mock_elevenlabs_cls.assert_called_once_with(api_key="literal-key")


def test_api_key_env_pointing_to_unset_var_raises(monkeypatch):
    monkeypatch.delenv("MY_TTS_KEY", raising=False)
    config = {"type": "elevenlabs", "api_key_env": "MY_TTS_KEY", "voice_id": "v"}
    with pytest.raises(ConfigError, match="MY_TTS_KEY"):
        ElevenLabsModule(config)


async def _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config) -> dict:
    """Run one stream() and return the kwargs the module sent to the SDK.

    Provider configuration is observed through the arguments sent to the
    provider client (testing.md), never through private attributes.
    """
    mock_stream_any.return_value = iter([])
    mock_client = mock_elevenlabs_cls.return_value
    mock_client.text_to_speech.stream.return_value = iter([b"mp3"])
    module = ElevenLabsModule(config)
    await module.stream("hello", TTSOptions(), MagicMock())
    return mock_client.text_to_speech.stream.call_args.kwargs


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_defaults_reach_the_provider_client(mock_elevenlabs_cls, mock_stream_any):
    kwargs = await _provider_stream_kwargs(
        mock_elevenlabs_cls, mock_stream_any, VALID_CONFIG
    )
    assert kwargs["voice_id"] == "test-voice-id"
    assert kwargs["model_id"] == "eleven_flash_v2_5"
    assert kwargs["output_format"] == "mp3_44100_128"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.5, similarity_boost=0.75
    )


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_custom_values_reach_the_provider_client(
    mock_elevenlabs_cls, mock_stream_any
):
    config = {
        **VALID_CONFIG,
        "model": "eleven_multilingual_v2",
        "stability": 0.8,
        "similarity_boost": 0.9,
    }
    kwargs = await _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config)
    assert kwargs["model_id"] == "eleven_multilingual_v2"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.8, similarity_boost=0.9
    )


@pytest.mark.parametrize(
    "override, field",
    [
        ({"api_key": 123}, "api_key"),
        ({"api_key_env": 5}, "api_key_env"),
        ({"voice_id": 42}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 7}, "model"),
        ({"stability": 5}, "stability"),
        ({"stability": True}, "stability"),
        ({"similarity_boost": -0.1}, "similarity_boost"),
        ({"similarity_boost": "x"}, "similarity_boost"),
    ],
)
def test_invalid_field_raises_config_error(override, field):
    with pytest.raises(ConfigError, match=field):
        ElevenLabsModule({**VALID_CONFIG, **override})


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_stream_calls_callback_with_decoded_pcm_bytes(
    mock_elevenlabs_cls, mock_stream_any
):
    pcm_chunks = [_array.array("h", [10, 20]), _array.array("h", [30, 40])]
    mock_client = MagicMock()
    mock_client.text_to_speech.stream.return_value = iter([b"mp3data"])
    mock_elevenlabs_cls.return_value = mock_client
    mock_stream_any.return_value = iter(pcm_chunks)

    module = ElevenLabsModule(VALID_CONFIG)
    callback = MagicMock()
    asyncio.run(module.stream("hello", TTSOptions(), callback))

    assert callback.call_count == 2
    callback.assert_any_call(pcm_chunks[0].tobytes())
    callback.assert_any_call(pcm_chunks[1].tobytes())


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_stream_skips_empty_elevenlabs_chunks(mock_elevenlabs_cls, mock_stream_any):
    # Drain the _ChunkSource via read() to inspect what bytes reached miniaudio
    all_data = bytearray()

    def capture_source(source, **kwargs):
        while chunk := source.read(1024):
            all_data.extend(chunk)
        return iter([])

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.return_value = iter([b"data", b"", b"more"])
    mock_elevenlabs_cls.return_value = mock_client
    mock_stream_any.side_effect = capture_source

    module = ElevenLabsModule(VALID_CONFIG)
    asyncio.run(module.stream("hello", TTSOptions(), MagicMock()))

    assert bytes(all_data) == b"datamore"


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_stream_wraps_elevenlabs_exception_in_tts_error(
    mock_elevenlabs_cls, mock_stream_any
):
    # Trigger source consumption so the SDK exception propagates through _ChunkSource.read()
    mock_stream_any.side_effect = lambda source, **kwargs: source.read(1) and iter([])

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.side_effect = RuntimeError("network failure")
    mock_elevenlabs_cls.return_value = mock_client

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(TTSError, match="network failure"):
        asyncio.run(module.stream("hello", TTSOptions(), MagicMock()))


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_stream_does_not_mask_callback_errors(mock_elevenlabs_cls, mock_stream_any):
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])
    mock_stream_any.return_value = iter([_array.array("h", [1, 2])])

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.stream("hello", TTSOptions(), bad_callback))


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_cancel_stops_callbacks_before_stream_raises(
    mock_elevenlabs_cls, mock_stream_any
):
    # An endless decoder: without cooperative cancellation the worker thread
    # would keep calling back forever after stream() has raised.
    def endless_chunks(*args, **kwargs):
        while True:
            time.sleep(0.005)
            yield _array.array("h", [1])

    mock_stream_any.side_effect = endless_chunks
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    module = ElevenLabsModule(VALID_CONFIG)
    task = asyncio.create_task(module.stream("hello", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)  # ~10 chunk periods: a leaked worker would show
    assert count == settled
