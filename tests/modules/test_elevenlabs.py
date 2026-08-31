"""Unit tests for ElevenLabsModule."""
import array as _array
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from tts_mcp.config import ConfigError
from tts_mcp.modules.base import TTSError, TTSOptions
from tts_mcp.modules.elevenlabs import ElevenLabsModule

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


@patch("tts_mcp.modules.elevenlabs.ElevenLabs")
def test_defaults(mock_elevenlabs_cls):
    module = ElevenLabsModule(VALID_CONFIG)
    assert module._model == "eleven_flash_v2_5"
    assert module._stability == 0.5
    assert module._similarity_boost == 0.75


@patch("tts_mcp.modules.elevenlabs.ElevenLabs")
def test_custom_values(mock_elevenlabs_cls):
    config = {**VALID_CONFIG, "model": "eleven_multilingual_v2", "stability": 0.8, "similarity_boost": 0.9}
    module = ElevenLabsModule(config)
    assert module._model == "eleven_multilingual_v2"
    assert module._stability == 0.8
    assert module._similarity_boost == 0.9


@patch("tts_mcp.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_mcp.modules.elevenlabs.ElevenLabs")
def test_stream_calls_callback_with_decoded_pcm_bytes(mock_elevenlabs_cls, mock_stream_any):
    pcm_chunks = [_array.array('h', [10, 20]), _array.array('h', [30, 40])]
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


@patch("tts_mcp.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_mcp.modules.elevenlabs.ElevenLabs")
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


@patch("tts_mcp.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_mcp.modules.elevenlabs.ElevenLabs")
def test_stream_wraps_elevenlabs_exception_in_tts_error(mock_elevenlabs_cls, mock_stream_any):
    # Trigger source consumption so the SDK exception propagates through _ChunkSource.read()
    mock_stream_any.side_effect = lambda source, **kwargs: (source.read(1) and iter([]))

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.side_effect = RuntimeError("network failure")
    mock_elevenlabs_cls.return_value = mock_client

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(TTSError, match="network failure"):
        asyncio.run(module.stream("hello", TTSOptions(), MagicMock()))
