"""Shared support for the opt-in live/e2e tier.

Holds the subprocess/config helpers the live tests share, plus `require_env` —
the skip-not-fail guard so a test with no credentials skips cleanly instead of
failing (you only exercise the services you hold keys for).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import tempfile
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def require_env(name: str) -> str:
    """Return env var `name`, or skip the calling test if it's unset/empty."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; skipping live test")
    return value


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    """Poll until a TCP connection to host:port succeeds."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            _, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Server on {host}:{port} did not start within {timeout}s")


async def start_mcp_server(config: dict, port: int) -> tuple[asyncio.subprocess.Process, str]:
    """Start a tts-engine-mcp subprocess.  Returns (process, tmp_config_path)."""
    cfg = {**config, "server": {"host": "127.0.0.1", "port": port}}

    fd, config_path = tempfile.mkstemp(suffix=".json", prefix="tts_engine_e2e_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)

    log.info("Starting MCP server subprocess on port %d (config: %s)", port, config_path)
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "tts-engine-mcp",
        "--config", config_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        await _wait_for_port("127.0.0.1", port)
    except TimeoutError:
        proc.terminate()
        await proc.wait()
        os.unlink(config_path)
        raise

    log.info("MCP server subprocess ready on port %d (pid %d)", port, proc.pid)
    return proc, config_path


async def stop_mcp_server(proc: asyncio.subprocess.Process, config_path: str) -> None:
    """Terminate the MCP server subprocess and clean up the temp config."""
    log.info("Stopping MCP server subprocess (pid %d)", proc.pid)
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        log.info("MCP server subprocess stopped cleanly")
    except TimeoutError:
        log.warning("MCP server subprocess did not stop within timeout — killing")
        proc.kill()
        await proc.wait()
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass
