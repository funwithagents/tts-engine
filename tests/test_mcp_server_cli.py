"""Tests for the MCP server entry point: main() wires config into the server.

The default tier never starts a real server (that is the opt-in e2e test_mcp.py);
here we drive main() with the real load_config and patched boundary collaborators,
asserting only the wiring that can break — the values threaded from config into
setup_logging and uvicorn.run, and the engine/app passed between the layers.
"""

import json

import pytest

from tts_engine.config import load_config
from tts_engine.mcp_server_cli import main

_CONFIG = {
    "engine": {
        "module": {
            "type": "elevenlabs",
            "api_key_env": "ELEVENLABS_API_KEY",
            "voice_id": "JBFqnCBsd6RMkjVDRZzb",
            "model": "eleven_flash_v2_5",
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
        "player": {"device": None},
    },
    # Distinctive, non-default values so the assertions can't pass by coincidence.
    "server": {"host": "0.0.0.0", "port": 9123},
    "logging": {"level": "WARNING"},
}


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_CONFIG), encoding="utf-8")
    return path


def test_main_wires_config_into_server(config_path, mocker):
    setup_logging = mocker.patch("tts_engine.mcp_server_cli.setup_logging")
    engine_cls = mocker.patch("tts_engine.mcp_server_cli.TTSEngine")
    create_server = mocker.patch("tts_engine.mcp_server_cli.create_server")
    uvicorn_run = mocker.patch("tts_engine.mcp_server_cli.uvicorn.run")
    mocker.patch("sys.argv", ["tts-engine-mcp", "--config", str(config_path)])

    main()

    expected = load_config(str(config_path))
    # Level from the logging block reaches setup_logging.
    setup_logging.assert_called_once_with("WARNING")
    # The engine is built from the parsed engine config, then handed to the server.
    engine_cls.assert_called_once_with(expected.engine)
    create_server.assert_called_once_with(engine_cls.return_value)
    # The server's ASGI app runs under uvicorn on the configured host/port.
    uvicorn_run.assert_called_once_with(
        create_server.return_value.streamable_http_app.return_value,
        host="0.0.0.0",
        port=9123,
    )


def test_main_requires_config(mocker):
    mocker.patch("sys.argv", ["tts-engine-mcp"])
    with pytest.raises(SystemExit):
        main()
