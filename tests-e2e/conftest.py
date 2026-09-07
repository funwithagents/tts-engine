"""Shared fixtures for e2e tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from support import (
    default_module,
    find_free_port,
    start_mcp_server,
    stop_mcp_server,
)


@pytest.fixture
async def server_url():
    module_type, module_config = default_module()
    port = find_free_port()
    proc, config_path = await start_mcp_server(module_type, module_config, port)
    yield f"http://127.0.0.1:{port}/mcp"
    await stop_mcp_server(proc, config_path)
