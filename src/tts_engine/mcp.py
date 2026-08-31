"""MCP server: speak tool, StreamableHTTP transport.

A thin transport layer: each tool wrapper delegates to the transport-agnostic
`tools` layer; the synthesis/validation logic lives there, not here.
"""
from mcp.server.fastmcp import FastMCP

from tts_engine import tools
from tts_engine.engine import TTSEngine


def create_server(engine: TTSEngine) -> FastMCP:
    mcp = FastMCP("tts-engine")

    @mcp.tool()
    async def speak(text: str) -> str:
        """Synthesize text and play it via the configured TTS engine."""
        return await tools.speak(engine, text)

    return mcp
