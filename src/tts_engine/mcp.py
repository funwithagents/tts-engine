"""MCP server: say tool, StreamableHTTP transport.

A thin transport layer: each tool wrapper delegates to the transport-agnostic
`TTSTools` layer; the synthesis/validation logic lives there, not here.
"""

from mcp.server.fastmcp import FastMCP

from tts_engine.engine import TTSEngine
from tts_engine.tools import TTSTools


def create_server(
    engine: TTSEngine, *, host: str = "127.0.0.1", port: int = 8000
) -> FastMCP:
    """Build the server for a deployment bound to ``host``/``port``.

    The bind address goes to FastMCP because it decides the Host-header
    policy: for a loopback bind the SDK enables DNS-rebinding protection
    (only ``localhost`` / ``127.0.0.1`` / ``[::1]`` Hosts are accepted); for
    any other address it leaves requests unfiltered, so a server bound to a
    LAN interface is reachable through that address.
    """
    mcp = FastMCP("tts-engine", host=host, port=port)
    tools = TTSTools(engine)

    @mcp.tool()
    async def say(text: str) -> str:
        """Synthesize text and play it via the configured TTS engine."""
        return await tools.say(text)

    return mcp
