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
    engine_raw = data["engine"]

    module_raw = engine_raw.get("module")
    if not isinstance(module_raw, dict):
        raise ConfigError("'engine.module' must be an object")
    module_type = module_raw.get("type")
    if not module_type or not isinstance(module_type, str):
        raise ConfigError("'engine.module.type' must be a non-empty string")

    player_raw = engine_raw.get("player", {})
    engine_cfg = TTSEngineConfig(
        module=dict(module_raw),
        player=PlayerConfig(device=player_raw.get("device", None)),
    )

    server_raw = data.get("server", {})
    port = server_raw.get("port", 8000)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ConfigError(
            f"'server.port' must be an integer in range 1–65535, got {port!r}"
        )
    server_cfg = ServerConfig(host=server_raw.get("host", "127.0.0.1"), port=port)

    return AppConfig(engine=engine_cfg, server=server_cfg)
