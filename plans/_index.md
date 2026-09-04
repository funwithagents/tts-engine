# Implementation plans

Implementation plans for TTS Engine — each plan turns a settled part of a spec (see [specs/_index.md](../specs/_index.md)) into concrete, buildable steps. Plans are ordered by their date-time filename prefix (`YYYYMMDDHHmm_`); new plans start from [_plan-template.md](_plan-template.md).

## Plans

<!-- One row per plan, chronological by filename prefix. Keep the Status column in sync with each plan's `**Status:**` line. -->

| Plan | Description | Status |
|---|---|---|
| [Project Setup](202603261502_project-setup.md) | `uv` init, dependencies, source tree, entry points | Done |
| [Configuration](202603261503_config.md) | Config dataclasses, loading, validation, CLI arg parsing | Done |
| [Audio Player](202603261504_audio-player.md) | `sounddevice` output stream, `AudioPlayer`, PCM feed + drain | Done |
| [TTS Module Interface](202603261505_tts-module-interface.md) | ABC, `TTSOptions`, `TTSError`, registry | Done |
| [ElevenLabs Module](202603261506_elevenlabs-module.md) | Streaming PCM via ElevenLabs SDK, config, error handling | Done |
| [TTS Engine](202603261507_tts-engine.md) | Wires module + player, `speak()` entry point | Done |
| [MCP Server](202603261508_mcp-server.md) | `speak` tool, StreamableHTTP, startup wiring | Done |
| [E2E Testing](202603261509_e2e-testing.md) | In-process server, `speak` tool call, no-error assertion | Done |
| [Rename + library structure](202608311000_rename-and-library-structure.md) | `tts_mcp`→`tts_engine`, `tts-engine-mcp` entry point, tools/mcp split, package logger, public API | Done |
| [Nested config + from_config](202608311001_nested-config-and-from-config.md) | `engine`/`server`/`logging` schema, config dataclasses, `TTSEngine.from_config`, config-driven logging level | Done |
| [API key env + e2e split](202608311500_api-key-env-and-e2e-split.md) | `api_key_env` config field; split live tier into `test_engine.py` (library) + `test_mcp.py` (MCP) | Done |
| [Config-only constructor](202608312030_config-constructor.md) | Collapse `TTSEngine.__init__(module, player)` + `from_config` into a single `TTSEngine(config)` | Done |
| [Library logging + drop config level](202609011200_library-logging-drop-config-level.md) | `NullHandler` on the package logger; move log level from config to the `--log-level` flag | Done |
| [Module-declared sample rate](202609011532_module-declared-sample-rate.md) | `TTSModule.sample_rate` property; `AudioPlayer(sample_rate=...)`; engine wires the two | Done |
| [Pluggable audio sink](202609041557_pluggable-audio-sink.md) | `AudioSink` Protocol; constructor sink injection; `TTSEngine.sample_rate`; lazy sounddevice import | Done |
| [Rename speak → say](202609041632_rename-speak-to-say.md) | Rename the operation to `say` across engine, tools, MCP tool, tests, specs, and docs | Done |
| [Engine config from_dict](202609041741_engine-config-from-dict.md) | Extract `engine`-block validation into `TTSEngineConfig.from_dict`; `load_config` delegates to it | Done |

## Status legend

- **Todo** — written, not yet started
- **In progress** — actively being implemented
- **Done** — implemented, verified (lint/type-check/tests pass), and merged
