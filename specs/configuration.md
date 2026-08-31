---
code:
  - config.example.json
  - src/tts_engine/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Config file

The MCP server is started with `--config <path>` pointing to a JSON object. There is no default path — the argument is required. Library callers can load the same file with `load_config(path)` or construct the dataclasses directly.

`config.example.json` in the repo root documents all fields with placeholder values and must be kept in sync with this spec.

## Top-level structure

```json
{
  "engine": { ... },
  "server": { ... },
  "logging": { ... }
}
```

`engine` is required. `server` and `logging` are optional (they have defaults) — the MCP entry point uses `server`; a pure library caller may omit both.

`load_config(path)` returns an `AppConfig`:

```python
@dataclass
class AppConfig:
    engine: TTSEngineConfig
    server: ServerConfig
    logging: LoggingConfig
```

---

## `engine` block → `TTSEngineConfig`

Everything needed to build a `TTSEngine`: the TTS module config and the audio player config.

```json
"engine": {
  "module": { "type": "elevenlabs", "api_key": "...", "voice_id": "...", ... },
  "player": { "device": null }
}
```

```python
@dataclass
class TTSEngineConfig:
    module: dict[str, Any]  # raw module block, including "type"; parsed by the module
    player: PlayerConfig
```

`TTSEngine.from_config(engine_config)` consumes this (see [architecture.md](architecture.md)).

### `engine.module` block

Selects and configures the active TTS module.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Module identifier (e.g. `"elevenlabs"`). Must match a key in the module registry. |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module itself. |

Unknown fields beyond `type` are passed to the module constructor as-is; the module validates them. `TTSEngineConfig.module` carries this block through verbatim (a raw `dict`), matching the `TTSModule.__init__(config: dict)` contract in [tts-module-interface.md](tts-module-interface.md).

### `engine.player` block → `PlayerConfig`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `device` | string \| int \| null | no | `null` | `sounddevice` output device. `null` = system default. String = device name substring match. Integer = device index. |

```python
@dataclass
class PlayerConfig:
    device: str | int | None = None
```

---

## `server` block → `ServerConfig`

Consumed only by the MCP entry point.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `host` | non-empty string | no | `"127.0.0.1"` | Bind address for the StreamableHTTP server. |
| `port` | int | no | `8000` | TCP port. |

---

## `logging` block → `LoggingConfig`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `level` | string | no | `"INFO"` | Log level for the package logger (`tts_engine`). One of the standard names: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

```python
@dataclass
class LoggingConfig:
    level: str = "INFO"
```

The level is applied by `setup_logging(level)` to the package logger `logging.getLogger("tts_engine")` — never the root logger (see [project.md](project.md), "Logging").

---

## Example

```json
{
  "engine": {
    "module": {
      "type": "elevenlabs",
      "api_key_env": "ELEVENLABS_API_KEY",
      "voice_id": "JBFqnCBsd6RMkjVDRZzb",
      "model": "eleven_flash_v2_5",
      "stability": 0.5,
      "similarity_boost": 0.75
    },
    "player": {
      "device": null
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "logging": {
    "level": "INFO"
  }
}
```

## Validation rules

- File-read failures and invalid JSON raise `ConfigError` with the file path.
- The top-level value and the `engine`, `engine.module`, `engine.player`, `server`, and `logging` blocks must be JSON objects. Shape failures raise `ConfigError`, never raw `AttributeError`/`TypeError`.
- The `engine` block is required and must contain a `module` block. Missing required blocks/fields raise `ConfigError` with a message identifying the missing key.
- `load_config` validates `engine.module.type` as a non-empty string. Registry membership is validated later by `load_module` during `TTSEngine.from_config`, avoiding a config↔module import cycle and allowing callers to register custom modules before constructing the engine. Unknown values still raise `ConfigError` before an engine is created.
- `engine.player.device` must be a string, an integer other than `bool`, or `null`.
- `server.host` must be a non-empty string; `server.port` must be an integer other than `bool` in range 1–65535.
- `logging.level` must be a recognized level name; an unknown level raises `ConfigError`.
