"""Shared fixtures for e2e tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from support import (
    CONFIG_PATH,
    find_free_port,
    load_config,
    start_mcp_server,
    stop_mcp_server,
)


@pytest.fixture
async def server_url():
    if not CONFIG_PATH.exists():
        pytest.skip("config.json not found — skipping e2e tests")

    config = load_config()
    port = find_free_port()
    proc, config_path = await start_mcp_server(config, port)
    yield f"http://127.0.0.1:{port}/mcp"
    await stop_mcp_server(proc, config_path)
