"""Tests for the MCP server: the say tool is a thin wrapper over tools.say."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import TextContent

from tts_engine.engine import TTSEngine
from tts_engine.mcp import create_server
from tts_engine.tools import TTSTools


@pytest.fixture
def mock_engine():
    engine = MagicMock(spec=TTSEngine)
    engine.say = AsyncMock()
    return engine


async def _call_say(mcp, text):
    content, _ = await mcp.call_tool("say", {"text": text})
    block = content[0]
    assert isinstance(block, TextContent)
    return block.text


async def test_say_tool_delegates_to_tools_say(mock_engine, mocker):
    delegate = mocker.patch.object(
        TTSTools, "say", new=AsyncMock(return_value="sentinel")
    )
    result = await _call_say(create_server(mock_engine), "hello")

    assert result == "sentinel"
    delegate.assert_awaited_once_with("hello")


async def test_say_tool_returns_ok_on_success(mock_engine):
    result = await _call_say(create_server(mock_engine), "hello")

    mock_engine.say.assert_awaited_once_with("hello")
    assert result == "OK"
