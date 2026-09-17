"""MCP entry point: parses args, checks the mcp extra, and wires everything together."""

import argparse
import logging

from tts_engine.config import MCPServerConfig
from tts_engine.engine import TTSEngine

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_MCP_EXTRA_MODULES = {"mcp", "uvicorn"}
_MCP_EXTRA_HINT = "tts-engine-mcp requires the mcp extra: pip install tts-engine[mcp]"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tts-engine-mcp",
        description="TTS Engine MCP server — exposes the engine's say tool over StreamableHTTP",
    )
    parser.add_argument(
        "--config", required=True, metavar="PATH", help="Path to config JSON file"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="Root log level for the server process (default: INFO)",
    )
    args = parser.parse_args()

    # The console script is installed even without the mcp extra, so the MCP
    # stack is imported here: a missing extra becomes an install hint, not a
    # traceback. Only a missing top-level package means the extra is absent; a
    # missing submodule (e.g. an incompatible mcp version) or any other import
    # error is a real problem and is re-raised.
    try:
        import uvicorn

        from tts_engine.mcp import create_server
    except ModuleNotFoundError as exc:
        if exc.name not in _MCP_EXTRA_MODULES:
            raise
        raise SystemExit(_MCP_EXTRA_HINT) from exc

    cfg = MCPServerConfig.from_json_file(args.config)
    logging.basicConfig(level=args.log_level, format=_LOG_FORMAT)
    log.info(
        "Config loaded: module.type=%s host=%s port=%d",
        cfg.engine.module["type"],
        cfg.server.host,
        cfg.server.port,
    )

    engine = TTSEngine(cfg.engine)
    mcp_app = create_server(engine)

    uvicorn.run(
        mcp_app.streamable_http_app(),
        host=cfg.server.host,
        port=cfg.server.port,
    )
