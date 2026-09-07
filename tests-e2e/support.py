"""Shared support for the opt-in live/e2e tier.

Holds the hardcoded per-module configs (no committed `config.json`), the skip
gates so a test with no credentials/extra skips cleanly instead of failing, and
the subprocess helpers the live tests share.

Configs live here in code, asr-engine style: `default_module()` picks the single
backend the module-agnostic tests drive, and `MODULES` is the per-backend table
the parametrized conformance test iterates. Both share one source for the
reference backend's config.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import socket
import tempfile

import pytest

log = logging.getLogger(__name__)


class CaptureSink:
    """In-memory `AudioSink`: keeps the synthesized PCM instead of playing it.

    Lets the per-module conformance test assert that a backend produced whole
    signed-16-bit samples and drained once, with no audio hardware in the loop.
    """

    def __init__(self) -> None:
        self.data = bytearray()
        self.drains = 0

    def feed(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    def drain(self) -> None:
        self.drains += 1


# module type → the importable library whose packaging extra gates that backend.
# Base backends (elevenlabs) are always installed and declare nothing here; a
# local-model backend behind an extra (e.g. a future `pocket` needing the
# `pocket_tts` library) lists it so `require_module` can skip when the extra is
# absent. See specs/project.md, "Dependency strategy for TTS backends".
_REQUIRED_IMPORT: dict[str, str] = {}


def require_module(module_type: str, config: dict) -> None:
    """Skip the calling live test unless *this* backend can actually run.

    Backends gate differently, so this generalizes the API-key check:

    - an API backend without its key — `config["api_key_env"]` names an unset
      variable — skips (opt-in live tier; keys may live in ``~/.zshrc``, which a
      non-interactive shell doesn't source);
    - a local-model backend whose packaging extra isn't installed — its
      `_REQUIRED_IMPORT` library isn't importable — skips.

    A keyless backend whose library is present is never skipped.
    """
    env_name = config.get("api_key_env")
    if env_name and not os.environ.get(env_name):
        pytest.skip(f"{env_name} not set; skipping live test for {module_type!r}")

    lib = _REQUIRED_IMPORT.get(module_type)
    if lib and importlib.util.find_spec(lib) is None:
        pytest.skip(
            f"{lib!r} not importable; install the {module_type!r} extra to run "
            f"its live test (pip install tts-engine[{module_type}])"
        )


# The reference backend's config, shared by `default_module()` and the
# `elevenlabs` row of `MODULES` so there is one source of truth. It carries
# `api_key_env` (the *name* of the env var holding the key), never the key
# itself — the live tier is turned on by exporting that variable.
_ELEVENLABS_CONFIG = {
    "api_key_env": "ELEVENLABS_API_KEY",
    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "model": "eleven_flash_v2_5",
}


def default_module() -> tuple[str, dict]:
    """Module type + config for the module-agnostic live tests (real audio
    hardware in `test_engine.py`, MCP transport in `test_mcp.py`).

    The single place the default backend is chosen, so none of those tests
    hardcodes one. Skips (via `require_module`) when its key env var is unset.
    """
    module_type, config = "elevenlabs", _ELEVENLABS_CONFIG
    require_module(module_type, config)
    return module_type, config


# Per-module config table for the parametrized conformance test
# (`test_modules.py`). Each row carries the module type and its own dedicated
# config; the module identity is the parametrize id. Add a row when adding a TTS
# module so it gets live conformance coverage. `api_key_env` (not a literal key)
# and any extra-gated library (via `_REQUIRED_IMPORT`) decide when a row skips.
MODULES = [
    pytest.param("elevenlabs", _ELEVENLABS_CONFIG, id="elevenlabs"),
]


def engine_block(module_type: str, module_config: dict) -> dict:
    """The `engine` config block for a `(type, config)` pair — the shape both
    `TTSEngineConfig.from_dict` (in-process) and the MCP subprocess consume."""
    return {
        "module": {"type": module_type, **module_config},
        "player": {"device": None},
    }


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


async def start_mcp_server(
    module_type: str, module_config: dict, port: int
) -> tuple[asyncio.subprocess.Process, str]:
    """Start a tts-engine-mcp subprocess for a `(type, config)` pair.

    Writes a temp config built from the hardcoded module config (no committed
    file) and spawns the real binary against it. Returns (process, tmp_config_path).
    """
    cfg = {
        "engine": engine_block(module_type, module_config),
        "server": {"host": "127.0.0.1", "port": port},
    }

    fd, config_path = tempfile.mkstemp(suffix=".json", prefix="tts_engine_e2e_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)

    log.info(
        "Starting MCP server subprocess on port %d (config: %s)", port, config_path
    )
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "tts-engine-mcp",
        "--config",
        config_path,
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
