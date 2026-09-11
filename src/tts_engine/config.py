"""Config dataclasses (`TTSEngineConfig`, `MCPServerConfig`) + `ConfigError`.

Each public config exposes a symmetric constructor trio, layered file → json →
dict: `from_json_file(path)` reads a file and delegates to `from_json(text)`,
which parses JSON and delegates to `from_dict(data)`, which validates and builds.
"""

import json
from dataclasses import dataclass, field
from typing import Any


class ConfigError(Exception):
    pass


def _loads(text: str, source: str | None = None) -> Any:
    """Parse JSON, re-raising a decode failure as ConfigError (naming ``source``)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        where = f" in {source}" if source else ""
        raise ConfigError(f"Invalid JSON{where}: {e}") from e


@dataclass
class PlayerConfig:
    device: str | int | None = None


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class TTSEngineConfig:
    module: dict[str, Any]  # raw module block, including "type"; parsed by the module
    player: PlayerConfig = field(default_factory=PlayerConfig)

    @classmethod
    def from_dict(cls, engine_block: dict[str, Any]) -> "TTSEngineConfig":
        """Build (and validate) a TTSEngineConfig from a raw ``engine`` block dict.

        Structural validation: ``engine`` must be an object, ``module`` an object,
        ``module.type`` a non-empty string, and ``player.device`` a
        str | int (not bool) | None. The ``module`` block is carried through
        verbatim as a raw dict; no environment variables are read here (the
        module resolves its own credentials later at engine construction).
        Raises ConfigError on shape failures.
        """
        if not isinstance(engine_block, dict):
            raise ConfigError("'engine' must be an object")

        module_raw = engine_block.get("module")
        if not isinstance(module_raw, dict):
            raise ConfigError("'engine.module' must be an object")
        module_type = module_raw.get("type")
        if not module_type or not isinstance(module_type, str):
            raise ConfigError("'engine.module.type' must be a non-empty string")

        player_raw = engine_block.get("player", {})
        if not isinstance(player_raw, dict):
            raise ConfigError("'engine.player' must be an object")
        device = player_raw.get("device", None)
        if device is not None and (
            isinstance(device, bool) or not isinstance(device, (str, int))
        ):
            raise ConfigError(
                "'engine.player.device' must be a string, an integer, or null"
            )

        return cls(module=dict(module_raw), player=PlayerConfig(device=device))

    @classmethod
    def from_json(cls, text: str) -> "TTSEngineConfig":
        """Build a TTSEngineConfig from a JSON string that *is* the engine block."""
        return cls.from_dict(_loads(text))

    @classmethod
    def from_json_file(cls, path: str) -> "TTSEngineConfig":
        """Build a TTSEngineConfig from a JSON file whose top-level object is the
        engine block (``module`` + ``player``), not a whole MCP-server config."""
        with open(path) as f:
            return cls.from_dict(_loads(f.read(), source=path))


@dataclass
class MCPServerConfig:
    """Everything the MCP server entry point needs: the engine wiring plus the
    StreamableHTTP ``server`` bind block. A pure library caller uses
    ``TTSEngineConfig`` directly and never touches this."""

    engine: TTSEngineConfig
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        """Build (and validate) an MCPServerConfig from a raw top-level config dict.

        Requires an ``engine`` block (delegated to ``TTSEngineConfig.from_dict``);
        ``server`` is optional and defaults. Raises ConfigError on shape failures.
        """
        if not isinstance(data, dict):
            raise ConfigError("config must be an object")
        if "engine" not in data:
            raise ConfigError("Missing required config block: 'engine'")
        engine_cfg = TTSEngineConfig.from_dict(data["engine"])

        server_raw = data.get("server", {})
        if not isinstance(server_raw, dict):
            raise ConfigError("'server' must be an object")
        host = server_raw.get("host", "127.0.0.1")
        if not isinstance(host, str) or not host:
            raise ConfigError("'server.host' must be a non-empty string")
        port = server_raw.get("port", 8000)
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not (1 <= port <= 65535)
        ):
            raise ConfigError(
                f"'server.port' must be an integer in range 1–65535, got {port!r}"
            )
        server_cfg = ServerConfig(host=host, port=port)

        return cls(engine=engine_cfg, server=server_cfg)

    @classmethod
    def from_json(cls, text: str) -> "MCPServerConfig":
        """Build an MCPServerConfig from a JSON string (top-level ``{engine, server}``)."""
        return cls.from_dict(_loads(text))

    @classmethod
    def from_json_file(cls, path: str) -> "MCPServerConfig":
        """Build an MCPServerConfig from a JSON config file (the ``--config`` file)."""
        with open(path) as f:
            return cls.from_dict(_loads(f.read(), source=path))
