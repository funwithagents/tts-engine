"""Tests for the tools layer."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_engine import TTSTools
from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.say = AsyncMock()
    return engine


@pytest.fixture
def tools(mock_engine):
    return TTSTools(mock_engine)


async def test_say_empty_text_returns_error_without_calling_engine(
    tools, mock_engine
):
    result = await tools.say("")

    assert result == "TTS error: text must not be empty"
    mock_engine.say.assert_not_called()


async def test_say_calls_engine_and_returns_ok(tools, mock_engine):
    result = await tools.say("hello")

    mock_engine.say.assert_awaited_once_with("hello")
    assert result == "OK"


async def test_say_catches_tts_error_and_returns_error_string(tools, mock_engine):
    mock_engine.say.side_effect = TTSError("synthesis failed")

    result = await tools.say("hello")

    assert result == "TTS error: synthesis failed"


def test_bound_method_is_registerable_as_a_tool(tools):
    """A bound method carries the name/doc a framework needs for a tool schema."""
    assert tools.say.__name__ == "say"
    assert tools.say.__doc__ is not None
    assert "Synthesize" in tools.say.__doc__
