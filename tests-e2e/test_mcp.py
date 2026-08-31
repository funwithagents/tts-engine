"""E2E test for the MCP interface: the `speak` tool over StreamableHTTP.

Drives the engine through the full MCP transport — a `tts-engine-mcp`
subprocess reached with a real MCP client. The in-process library path is
covered separately in `test_engine.py`.
"""

from __future__ import annotations

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


async def test_speak_returns_ok(server_url):
    async with (
        streamable_http_client(server_url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "speak", {"text": "Hello from the TTS MCP test"}
        )
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert block.text == "OK"
