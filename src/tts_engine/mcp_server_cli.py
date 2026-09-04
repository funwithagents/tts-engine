"""MCP entry point: parses args and wires everything together."""

import argparse
import logging

import uvicorn

from tts_engine.config import load_config
from tts_engine.engine import TTSEngine
from tts_engine.mcp import create_server

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


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

    cfg = load_config(args.config)
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
