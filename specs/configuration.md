---
code:
  - examples/config.elevenlabs.json
  - examples/config.pocket.json
  - examples/config.tone.json
  - examples/config.audiofile.json
  - src/tts_engine/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Config file

The MCP server is started with `--config <path>` pointing to a JSON object. There is no default path — the argument is required. The entry point parses it with `MCPServerConfig.from_json_file(path)`. Library callers can load the same file the same way and take `.engine`, or build the engine config directly from an in-memory `engine` block with `TTSEngineConfig.from_dict(engine_block)` (see "Constructors" below).

The `examples/` directory holds one complete config per module (`examples/config.<type>.json`, e.g. `config.elevenlabs.json`, `config.pocket.json`, `config.tone.json`, `config.audiofile.json`) with placeholder values, and must be kept in sync with this spec and the module specs. There is deliberately no single `config.example.json`: no module is the default (see [project.md](project.md), "Dependency strategy for TTS backends").

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
| `from_dict(data, *, base_dir=None)` | a parsed dict | validates and builds |
| `from_json(text, *, base_dir=None)` | a JSON string | parses, then delegates to `from_dict` |
| `from_json_file(path)` | a file path | reads the file, then delegates to `from_json`'s parse with `base_dir` set to the file's directory — an invalid-JSON error names the path |

**`base_dir`** is the directory relative file paths inside the config resolve against (the same idea as wica's `AgentConfig`/`WicaConfig` loaders). A dict or a JSON string has no location of its own, so `from_dict`/`from_json` take it as an optional keyword (`str | Path | None`); `from_json_file` supplies it from the config file's own directory, so a config file's relative paths are bound to where the file lives, not to the process working directory. With `base_dir=None`, relative paths are left as given and resolve against the working directory when used. How it reaches the module is described under "`engine.module` block" below.

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

The `module` block is carried through as a raw dict, with exactly one loader-side adjustment: the reserved `base_dir` key is absolutized / filled in as described under "`engine.module` block". **No environment variables are read** at this stage (the module resolves its own `api_key`/`api_key_env` later, at engine construction), so a config naming only an unset `api_key_env` still constructs. Use this to build an engine config from an in-memory dict — e.g. a host app composing several engine configs — without writing a temp file or duplicating validation; pass `base_dir=` when that dict names relative files.

### `engine.module` block

Selects and configures the active TTS module.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Module identifier (e.g. `"elevenlabs"`, `"pocket"`, `"tone"`, `"audiofile"`). Must match a key in the module registry. |
| `base_dir` | string | no | **Reserved key**, meaningful to every module: the directory that relative file paths in this block (a WAV file, a voice `.wav`) resolve against. Normally you omit it and the loader fills it in (below). |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module itself. |

`type` and `base_dir` are the only keys the config layer knows about; every other field is passed to the module constructor as-is and validated there. `TTSEngineConfig.module` carries the block through as a raw `dict`, matching the `TTSModule.__init__(config: dict)` contract in [tts-module-interface.md](tts-module-interface.md).

**`base_dir` rules** (applied by `TTSEngineConfig.from_dict`, given the loader's own `base_dir` argument, so the same rules hold whichever constructor was used):

1. If the block has a `base_dir`, it must be a non-empty string (`ConfigError` otherwise). If that value is a relative path **and** the loader has a `base_dir`, it is absolutized against the loader's: `str((loader_base_dir / value).resolve())`. If the loader has none, it is kept as given.
2. If the block has no `base_dir` and the loader has one, the loader's is written into the block: `module["base_dir"] = str(loader_base_dir.resolve())`.
3. If neither exists, the block stays without `base_dir`; modules then resolve relative paths against the working directory.

This is a *locate* step only — no file is read or checked to exist here; each module opens its own files in its constructor. Modules read the key through `resolve_path(config, value)` in `modules/base.py` ([tts-module-interface.md](tts-module-interface.md), "Path resolution"), so no module re-implements the rule. The engine and `TTSEngine` are unaware of it.

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
- `engine.module.base_dir`, when present, must be a non-empty string. It is absolutized / filled in per the `base_dir` rules above; whether the directory exists is not checked here.
- `engine.player.device` must be a string, an integer other than `bool`, or `null`.
- `server.host` must be a non-empty string; `server.port` must be an integer other than `bool` in range 1–65535.
