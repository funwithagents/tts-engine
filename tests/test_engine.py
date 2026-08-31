"""Tests for TTSEngine."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_mcp.engine import TTSEngine
from tts_mcp.modules.base import TTSError, TTSOptions


@pytest.fixture
def mock_module():
    module = MagicMock()
    module.stream = AsyncMock()
    return module


@pytest.fixture
def mock_player():
    player = MagicMock()
    return player


@pytest.fixture
def engine(mock_module, mock_player):
    return TTSEngine(module=mock_module, player=mock_player)


@pytest.mark.asyncio
async def test_speak_calls_stream_with_text_and_options(engine, mock_module, mock_player):
    await engine.speak("hello world")

    mock_module.stream.assert_awaited_once()
    args, kwargs = mock_module.stream.call_args
    assert args[0] == "hello world"
    assert isinstance(args[1], TTSOptions)
    assert kwargs.get("callback") == mock_player.feed


@pytest.mark.asyncio
async def test_speak_calls_drain_after_stream(engine, mock_module, mock_player):
    await engine.speak("hello")

    mock_module.stream.assert_awaited_once()
    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_speak_calls_drain_even_on_tts_error(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError):
        await engine.speak("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_speak_propagates_tts_error_after_drain(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError, match="synthesis failed"):
        await engine.speak("hello")

    mock_player.drain.assert_called_once()
