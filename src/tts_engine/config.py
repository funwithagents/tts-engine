"""Config dataclasses + load_config() + ConfigError."""

import json
from dataclasses import dataclass, field
from typing import Any


class ConfigError(Exception):
    pass


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

        Runs the same structural validation ``load_config`` applies to
        ``data["engine"]``: ``engine`` must be an object, ``module`` an object,
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


@dataclass
class AppConfig:
    engine: TTSEngineConfig
    server: ServerConfig = field(default_factory=ServerConfig)


def load_config(path: str) -> AppConfig:
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e

    if "engine" not in data:
        raise ConfigError("Missing required config block: 'engine'")
    engine_cfg = TTSEngineConfig.from_dict(data["engine"])

    server_raw = data.get("server", {})
    port = server_raw.get("port", 8000)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ConfigError(
            f"'server.port' must be an integer in range 1–65535, got {port!r}"
        )
    server_cfg = ServerConfig(host=server_raw.get("host", "127.0.0.1"), port=port)

    return AppConfig(engine=engine_cfg, server=server_cfg)
