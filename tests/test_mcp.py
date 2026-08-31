"""Tests for the MCP server: the speak tool is a thin wrapper over tools.speak."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_engine.engine import TTSEngine
from tts_engine.mcp import create_server


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.speak = AsyncMock()
    return engine


def _speak_tool(mcp):
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    return tools["speak"]


async def test_speak_tool_delegates_to_tools_speak(mock_engine, mocker):
    delegate = mocker.patch(
        "tts_engine.mcp.tools.speak", new=AsyncMock(return_value="sentinel")
    )
    speak = _speak_tool(create_server(mock_engine))

    result = await speak.fn(text="hello")

    assert result == "sentinel"
    delegate.assert_awaited_once_with(mock_engine, "hello")


async def test_speak_tool_returns_ok_on_success(mock_engine):
    speak = _speak_tool(create_server(mock_engine))

    result = await speak.fn(text="hello")

    mock_engine.speak.assert_awaited_once_with("hello")
    assert result == "OK"
