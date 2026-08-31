"""Tests for the MCP server speak tool."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_mcp.engine import TTSEngine
from tts_mcp.modules.base import TTSError
from tts_mcp.server import create_server


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.speak = AsyncMock()
    return engine


@pytest.fixture
def speak_tool(mock_engine):
    mcp = create_server(mock_engine)
    # Extract the registered speak tool's function
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    return tools["speak"], mock_engine


async def call_speak(speak_tool_fixture, text: str) -> str:
    tool, _engine = speak_tool_fixture
    result = await tool.fn(text=text)
    return result


async def test_speak_empty_text_returns_error_without_calling_engine(mock_engine):
    mcp = create_server(mock_engine)
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    speak = tools["speak"]

    result = await speak.fn(text="")

    assert "error" in result.lower()
    mock_engine.speak.assert_not_called()


async def test_speak_calls_engine_and_returns_ok(mock_engine):
    mcp = create_server(mock_engine)
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    speak = tools["speak"]

    result = await speak.fn(text="hello")

    mock_engine.speak.assert_awaited_once_with("hello")
    assert result == "OK"


async def test_speak_catches_tts_error_and_returns_error_string(mock_engine):
    mock_engine.speak.side_effect = TTSError("synthesis failed")
    mcp = create_server(mock_engine)
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    speak = tools["speak"]

    result = await speak.fn(text="hello")

    assert "synthesis failed" in result
    assert result.startswith("TTS error:")
