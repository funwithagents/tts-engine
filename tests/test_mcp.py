"""Tests for the MCP server: the speak tool is a thin wrapper over tools.speak."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from tts_engine.engine import TTSEngine
from tts_engine.mcp import create_server


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.speak = AsyncMock()
    return engine


async def _call_speak(mcp, text):
    content, _ = await mcp.call_tool("speak", {"text": text})
    block = content[0]
    assert isinstance(block, TextContent)
    return block.text


async def test_speak_tool_delegates_to_tools_speak(mock_engine, mocker):
    delegate = mocker.patch(
        "tts_engine.mcp.tools.speak", new=AsyncMock(return_value="sentinel")
    )
    result = await _call_speak(create_server(mock_engine), "hello")

    assert result == "sentinel"
    delegate.assert_awaited_once_with(mock_engine, "hello")


async def test_speak_tool_returns_ok_on_success(mock_engine):
    result = await _call_speak(create_server(mock_engine), "hello")

    mock_engine.speak.assert_awaited_once_with("hello")
    assert result == "OK"
