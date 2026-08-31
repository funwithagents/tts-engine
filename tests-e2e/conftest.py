"""Shared fixtures for e2e tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from support import (
    CONFIG_PATH,
    find_free_port,
    require_e2e_config,
    start_mcp_server,
    stop_mcp_server,
)


@pytest.fixture
async def server_url():
    config = require_e2e_config()
    port = find_free_port()
    proc, config_path = await start_mcp_server(config, port)
    yield f"http://127.0.0.1:{port}/mcp"
    await stop_mcp_server(proc, config_path)


@pytest.fixture
def app_config():
    """The committed e2e `config.json` parsed into an `AppConfig`, for driving
    the engine in-process. Skips when its credentials aren't available, like
    `server_url`."""
    require_e2e_config()

    from tts_engine import load_config as load_app_config

    return load_app_config(str(CONFIG_PATH))
