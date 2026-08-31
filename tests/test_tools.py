"""Tests for the tools layer."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_engine import tools
from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.speak = AsyncMock()
    return engine


async def test_speak_empty_text_returns_error_without_calling_engine(mock_engine):
    result = await tools.speak(mock_engine, "")

    assert result == "TTS error: text must not be empty"
    mock_engine.speak.assert_not_called()


async def test_speak_calls_engine_and_returns_ok(mock_engine):
    result = await tools.speak(mock_engine, "hello")

    mock_engine.speak.assert_awaited_once_with("hello")
    assert result == "OK"


async def test_speak_catches_tts_error_and_returns_error_string(mock_engine):
    mock_engine.speak.side_effect = TTSError("synthesis failed")

    result = await tools.speak(mock_engine, "hello")

    assert result == "TTS error: synthesis failed"
