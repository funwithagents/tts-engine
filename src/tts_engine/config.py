"""Config dataclasses (`TTSEngineConfig`, `MCPServerConfig`) + `ConfigError`.

Each public config exposes a symmetric constructor trio, layered file → json →
dict: `from_json_file(path)` reads a file and delegates to `from_json(text)`,
which parses JSON and delegates to `from_dict(data)`, which validates and builds.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Invalid configuration: bad JSON, a wrong shape, or a missing/invalid field.

    Subclasses ``ValueError`` — a malformed config is bad input data — so a
    caller composing this engine's config with others can catch one uniform
    "bad config" type.
    """


def _read(path: str) -> str:
    """Read a config file as UTF-8, whatever the platform's default encoding
    (a non-ASCII voice name must not depend on the locale); a file that is
    not valid UTF-8 is a ConfigError naming it."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError as e:
        raise ConfigError(f"Config file {path} is not valid UTF-8: {e}") from e


def _loads(text: str, source: str | None = None) -> Any:
    """Parse JSON, re-raising a decode failure as ConfigError (naming ``source``)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        where = f" in {source}" if source else ""
        raise ConfigError(f"Invalid JSON{where}: {e}") from e


# Bounds of `engine.gain_db`: wide enough for any level correction, narrow
# enough that a typo (75 for -7.5) fails instead of clipping everything.
GAIN_DB_MIN = -60.0
GAIN_DB_MAX = 24.0


def _is_finite(value: float) -> bool:
    """`math.isfinite`, with an integer too large for a float counted as not
    finite instead of raising OverflowError."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _check_gain_db(raw: Any) -> float:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not _is_finite(raw)
        or not (GAIN_DB_MIN <= raw <= GAIN_DB_MAX)
    ):
        raise ConfigError(
            f"'engine.gain_db' must be a finite number from {GAIN_DB_MIN:g} to "
            f"{GAIN_DB_MAX:g} dB, got {raw!r}"
        )
    return float(raw)


def _check_module(module: Any) -> None:
    if not isinstance(module, dict):
        raise ConfigError("'engine.module' must be an object")
    module_type = module.get("type")
    if not module_type or not isinstance(module_type, str):
        raise ConfigError("'engine.module.type' must be a non-empty string")
    if "base_dir" in module:
        raw = module["base_dir"]
        if not isinstance(raw, str) or not raw:
            raise ConfigError("'engine.module.base_dir' must be a non-empty string")


def _check_player(device: Any, discard: Any) -> None:
    if device is not None and (
        isinstance(device, bool) or not isinstance(device, (str, int))
    ):
        raise ConfigError(
            "'engine.player.device' must be a string, an integer, or null"
        )
    if not isinstance(discard, bool):
        raise ConfigError("'engine.player.discard' must be a boolean")
    if discard and device is not None:
        raise ConfigError(
            "'engine.player.discard' plays on no device: "
            "'engine.player.device' must be null or absent with it"
        )


def _check_server(host: Any, port: Any) -> None:
    if not isinstance(host, str) or not host:
        raise ConfigError("'server.host' must be a non-empty string")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ConfigError(
            f"'server.port' must be an integer in range 1–65535, got {port!r}"
        )


# The dataclasses validate themselves at construction (`__post_init__`), with
# the checks the `from_dict` loaders run on the raw blocks: a config built in
# Python is held to the same rules as one loaded from JSON. Assigning a field
# afterwards is not re-checked.


@dataclass
class PlayerConfig:
    device: str | int | None = None
    discard: bool = False  # no device: the audio is dropped at real-time pace

    def __post_init__(self) -> None:
        _check_player(self.device, self.discard)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        _check_server(self.host, self.port)


@dataclass
class TTSEngineConfig:
    module: dict[str, Any]  # raw module block, including "type"; parsed by the module
    player: PlayerConfig = field(default_factory=PlayerConfig)
    gain_db: float = 0.0  # static gain on the PCM fed to the sink; 0 = bit-exact

    def __post_init__(self) -> None:
        _check_module(self.module)
        if not isinstance(self.player, PlayerConfig):
            raise ConfigError("'engine.player' must be a PlayerConfig")
        self.gain_db = _check_gain_db(self.gain_db)

    @classmethod
    def from_dict(
        cls, engine_block: dict[str, Any], *, base_dir: str | Path | None = None
    ) -> "TTSEngineConfig":
        """Build (and validate) a TTSEngineConfig from a raw ``engine`` block dict.

        Structural validation: ``engine`` must be an object, ``module`` an object,
        ``module.type`` a non-empty string, ``module.base_dir`` (if present) a
        non-empty string, ``player.device`` a str | int (not bool) | None,
        ``player.discard`` a bool (``true`` only with no ``device``), and
        ``gain_db`` (if present) a finite number within GAIN_DB_MIN–GAIN_DB_MAX.

        The ``module`` block is carried through as a raw dict; ``type`` and
        ``base_dir`` are the only two keys the loader touches. ``base_dir`` (the
        directory relative file paths resolve against) is filled in from this
        call's ``base_dir`` when absent, or absolutized against it when relative.
        No environment variables are read here (the module resolves its own
        credentials later at engine construction). Raises ConfigError on shape
        failures.
        """
        if not isinstance(engine_block, dict):
            raise ConfigError("'engine' must be an object")

        module_raw: Any = engine_block.get("module")
        _check_module(module_raw)

        module: dict[str, Any] = dict(module_raw)
        loader_dir = Path(base_dir).resolve() if base_dir is not None else None
        if "base_dir" in module:
            raw = module["base_dir"]
            if loader_dir is not None and not Path(raw).is_absolute():
                module["base_dir"] = str((loader_dir / raw).resolve())
        elif loader_dir is not None:
            module["base_dir"] = str(loader_dir)

        player_raw = engine_block.get("player", {})
        if not isinstance(player_raw, dict):
            raise ConfigError("'engine.player' must be an object")
        device = player_raw.get("device", None)
        discard = player_raw.get("discard", False)
        _check_player(device, discard)

        return cls(
            module=module,
            player=PlayerConfig(device=device, discard=discard),
            gain_db=_check_gain_db(engine_block.get("gain_db", 0.0)),
        )

    @classmethod
    def from_json(
        cls, text: str, *, base_dir: str | Path | None = None
    ) -> "TTSEngineConfig":
        """Build a TTSEngineConfig from a JSON string that *is* the engine block."""
        return cls.from_dict(_loads(text), base_dir=base_dir)

    @classmethod
    def from_json_file(cls, path: str) -> "TTSEngineConfig":
        """Build a TTSEngineConfig from a JSON file whose top-level object is the
        engine block (``module``, ``player``, ``gain_db``), not a whole MCP-server config.
        Relative paths in the module block resolve against the file's directory."""
        return cls.from_dict(
            _loads(_read(path), source=path), base_dir=Path(path).resolve().parent
        )


@dataclass
class MCPServerConfig:
    """Everything the MCP server entry point needs: the engine wiring plus the
    StreamableHTTP ``server`` bind block. A pure library caller uses
    ``TTSEngineConfig`` directly and never touches this."""

    engine: TTSEngineConfig
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, base_dir: str | Path | None = None
    ) -> "MCPServerConfig":
        """Build (and validate) an MCPServerConfig from a raw top-level config dict.

        Requires an ``engine`` block (delegated to ``TTSEngineConfig.from_dict``);
        ``server`` is optional and defaults. ``base_dir`` is forwarded to the
        engine block (see ``TTSEngineConfig.from_dict``). Raises ConfigError on
        shape failures.
        """
        if not isinstance(data, dict):
            raise ConfigError("config must be an object")
        if "engine" not in data:
            raise ConfigError("Missing required config block: 'engine'")
        engine_cfg = TTSEngineConfig.from_dict(data["engine"], base_dir=base_dir)

        server_raw = data.get("server", {})
        if not isinstance(server_raw, dict):
            raise ConfigError("'server' must be an object")
        host = server_raw.get("host", "127.0.0.1")
        port = server_raw.get("port", 8000)
        _check_server(host, port)
        server_cfg = ServerConfig(host=host, port=port)

        return cls(engine=engine_cfg, server=server_cfg)

    @classmethod
    def from_json(
        cls, text: str, *, base_dir: str | Path | None = None
    ) -> "MCPServerConfig":
        """Build an MCPServerConfig from a JSON string (top-level ``{engine, server}``)."""
        return cls.from_dict(_loads(text), base_dir=base_dir)

    @classmethod
    def from_json_file(cls, path: str) -> "MCPServerConfig":
        """Build an MCPServerConfig from a JSON config file (the ``--config`` file).
        Relative paths in the module block resolve against the file's directory."""
        return cls.from_dict(
            _loads(_read(path), source=path), base_dir=Path(path).resolve().parent
        )
