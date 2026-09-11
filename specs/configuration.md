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

The MCP server is started with `--config <path>` pointing to a JSON object. There is no default path — the argument is required. The entry point parses it with `MCPServerConfig.from_json_file(path)`. Library callers can load the same file the same way and take `.engine`, or build the engine config directly from an in-memory `engine` block with `TTSEngineConfig.from_dict(engine_block)` (see "Constructors" below).

`config.example.json` in the repo root documents all fields with placeholder values and must be kept in sync with this spec.

## Top-level structure

```json
{
  "engine": { ... },
  "server": { ... }
}
```

`engine` is required. `server` is optional (it has defaults) and used only by the MCP entry point; a pure library caller may omit it. There is no logging block — the log level is an operational concern of the entry point, set via the MCP server's `--log-level` flag, not the config file (see [project.md](project.md), "Logging").

This top-level object is the MCP server's config, so it is modeled as `MCPServerConfig` (not a generic "app" config — the whole point of the layering is that the MCP is one interface, not the product). A pure library caller never touches it; it uses `TTSEngineConfig` directly.

```python
@dataclass
class MCPServerConfig:
    engine: TTSEngineConfig
    server: ServerConfig
```

## Constructors

Both public config dataclasses expose the **same symmetric trio**, layered file → json → dict so all three share one validation path (`from_dict`):

| Constructor | Input | Notes |
|---|---|---|
| `from_dict(data)` | a parsed dict | validates and builds |
| `from_json(text)` | a JSON string | parses, then delegates to `from_dict` |
| `from_json_file(path)` | a file path | reads the file, then delegates to `from_json`'s parse — an invalid-JSON error names the path |

- `MCPServerConfig.*` parse the **top-level** `{engine, server}` object (the `--config` file). `from_dict` requires `engine` (delegated to `TTSEngineConfig.from_dict`) and defaults `server`.
- `TTSEngineConfig.*` parse an **`engine` block on its own** — an object of `module` + `player`, *not* wrapped under an `"engine"` key. Use these when a library caller keeps an engine config in its own dict/string/file.

There is no free `load_config` function — `MCPServerConfig.from_json_file(path)` replaces it.

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

`TTSEngine(engine_config)` consumes this (see [architecture.md](architecture.md)).

#### `TTSEngineConfig.from_dict(engine_block)`

The sanctioned in-memory constructor: it takes the raw `engine` block (the object of `module` + `player`, **not** the whole file) and returns a validated `TTSEngineConfig`, running the structural validation in "Validation rules". `MCPServerConfig.from_dict` delegates its `data["engine"]` here, and `TTSEngineConfig.from_json` / `from_json_file` delegate here after parsing — so every path shares one validation path.

```python
engine_cfg = TTSEngineConfig.from_dict({"module": {"type": "elevenlabs", ...}, "player": {"device": null}})
```

The `module` block is carried through verbatim as a raw dict; **no environment variables are read** at this stage (the module resolves its own `api_key`/`api_key_env` later, at engine construction), so a config naming only an unset `api_key_env` still constructs. Use this to build an engine config from an in-memory dict — e.g. a host app composing several engine configs — without writing a temp file or duplicating validation.

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

## Logging

There is no `logging` config block. The log level is set at the process level by the `tts-engine-mcp` entry point's `--log-level` flag (default `INFO`), which the entry point applies via `logging.basicConfig(level=...)` (see [project.md](project.md), "Logging"). A library caller configures logging however its host application does; the library itself only attaches a `NullHandler`.

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
  }
}
```

## Validation rules

The `engine`-block rules below are enforced by `TTSEngineConfig.from_dict` and so apply identically whether reached via `MCPServerConfig` or a direct `TTSEngineConfig` constructor; the top-level and `server` rules are `MCPServerConfig.from_dict`'s alone. Invalid JSON raises `ConfigError`, and the `*_json_file` constructors include the file path in that message.

- `ConfigError` subclasses `ValueError` (a malformed config is invalid input data), so a caller can catch either `ConfigError` for the specific type or `ValueError` for any bad-config surface — including the module-level `ConfigError`s raised at `TTSEngine` construction.
- Invalid JSON raises `ConfigError` (with the file path when parsing a file).
- The top-level value and the `engine`, `engine.module`, `engine.player`, and `server` blocks must be JSON objects. Shape failures raise `ConfigError`, never raw `AttributeError`/`TypeError`.
- The `engine` block is required and must contain a `module` block. Missing required blocks/fields raise `ConfigError` with a message identifying the missing key.
- `engine.module.type` must be a non-empty string. Registry membership is validated later by `load_module` during `TTSEngine` construction, avoiding a config↔module import cycle and allowing callers to register custom modules before constructing the engine. Unknown values still raise `ConfigError` before an engine is created.
- `engine.player.device` must be a string, an integer other than `bool`, or `null`.
- `server.host` must be a non-empty string; `server.port` must be an integer other than `bool` in range 1–65535.
