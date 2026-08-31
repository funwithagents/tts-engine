---
code:
  - src/tts_mcp/config.py
tests:
  - tests/test_config.py
---

# Configuration

**Status:** Implemented

## Config file

The server is started with `--config <path>` pointing to a JSON file. There is no default path — the argument is required.

`config.example.json` in the repo root documents all fields with placeholder values and must be kept in sync with this spec.

## Top-level structure

```json
{
  "tts": { ... },
  "audio": { ... },
  "server": { ... }
}
```

All three top-level blocks are required.

---

## `tts` block

Selects and configures the active TTS module.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Module identifier (e.g. `"elevenlabs"`). Must match a key in the module registry. |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module itself. |

Unknown fields beyond `type` are passed to the module constructor as-is; the module is responsible for validating them.

---

## `audio` block

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `device` | string \| int \| null | no | `null` | `sounddevice` output device. `null` = system default. String = device name substring match. Integer = device index. |

---

## `server` block

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `host` | string | no | `"127.0.0.1"` | Bind address for the StreamableHTTP server. |
| `port` | int | no | `8000` | TCP port. |

---

## Example

```json
{
  "tts": {
    "type": "elevenlabs",
    "api_key": "sk_...",
    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "model": "eleven_flash_v2_5",
    "stability": 0.5,
    "similarity_boost": 0.75
  },
  "audio": {
    "device": null
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  }
}
```

## Validation rules

- `tts.type` must be a non-empty string matching a registered module key. Unknown values raise a `ConfigError` at startup.
- `server.port` must be in range 1–65535.
- Missing required fields raise `ConfigError` with a message identifying the missing key.
- The config file must be valid JSON; parse errors are reported with the file path.
