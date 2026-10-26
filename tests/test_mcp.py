"""Tests for the MCP server: the say tool is a thin wrapper over tools.say, and
the Host-header policy follows the configured bind address."""

from unittest.mock import AsyncMock, MagicMock

import httpx
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


# --- Host-header policy -------------------------------------------------------

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


async def _initialize_status(mcp, host_header: str) -> int:
    """POST an initialize request to the server's ASGI app, in process, with
    the given Host header; the status code is the server's Host policy."""
    app = mcp.streamable_http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
            response = await c.post(
                "/mcp",
                json=_INITIALIZE,
                headers={
                    "Host": host_header,
                    "Accept": "application/json, text/event-stream",
                },
            )
    return response.status_code


@pytest.mark.parametrize(
    ("bind", "host_header", "status"),
    [
        ("127.0.0.1", "localhost:8000", 200),
        ("127.0.0.1", "192.168.1.10:8000", 421),
        ("0.0.0.0", "192.168.1.10:8000", 200),
    ],
)
async def test_host_header_policy_follows_the_bind_address(
    mock_engine, bind, host_header, status
):
    # A loopback bind keeps the SDK's DNS-rebinding protection (loopback Hosts
    # only); a server bound to a LAN interface is reachable through its address.
    mcp = create_server(mock_engine, host=bind, port=8000)
    assert await _initialize_status(mcp, host_header) == status
