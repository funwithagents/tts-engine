# AGENTS.md — TTS MCP Project

## What this project is

An MCP server that exposes text-to-speech capabilities via a `speak` tool. It accepts text input, synthesizes speech through a pluggable TTS module (ElevenLabs first), and plays the audio in real-time on the server machine using streaming playback.

- **Language**: Python, project managed with `uv`
- **MCP SDK**: official Python SDK (`modelcontextprotocol/python-sdk`)
- **Transport**: StreamableHTTP — enables remote access on a local network
- **One active module**: a single TTS module is loaded at startup, selected via `tts.type` in config

## Key design decisions

- **Streaming playback**: audio is streamed from the TTS provider and fed to the audio device chunk-by-chunk, minimising latency before sound starts.
- **Callback-based streaming**: the module layer accepts a `callback: Callable[[bytes], None]` for each audio chunk — this decouples the module from the playback mechanism and makes the engine testable without audio hardware.
- **MP3 from ElevenLabs, decoded in-process**: the ElevenLabs module requests `mp3_44100_128` and decodes each chunk to signed-16 PCM mono via `miniaudio` before the callback, so `AudioPlayer` always receives PCM.
- **`speak` tool only (v1)**: no `synthesize`/file output, no `list_voices`, no MCP resources.
- **Pluggable modules**: the `tts` config block uses `type` to select the module; all other fields under `tts` are module-specific. Only one module is active at a time.
- **`sounddevice` for playback**: wraps PortAudio, best choice on Ubuntu; device is configurable via `audio.device` (`null` = system default).

## Repository layout

```
tts-mcp/
├── AGENTS.md                    # This file
├── pyproject.toml               # uv project: deps, entry points, pytest config
├── config.example.json          # Config template (no secrets)
├── specs/                       # Pre-implementation design docs (what & why)
│   ├── _index.md                # Index — start here
│   ├── _spec-template.md        # Copy this to start a new spec
│   ├── project.md               # Project structure & tooling
│   ├── testing.md               # Testing strategy
│   ├── overview.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── mcp-server.md
│   ├── tts-module-interface.md
│   ├── elevenlabs-module.md
│   └── audio-player.md
├── plans/                       # Implementation plans (how), YYYYMMDDHHmm_ prefixed
│   ├── _index.md                # Index
│   ├── _plan-template.md        # Copy this to start a new plan
│   ├── 202603261502_project-setup.md
│   ├── 202603261503_config.md
│   ├── 202603261504_audio-player.md
│   ├── 202603261505_tts-module-interface.md
│   ├── 202603261506_elevenlabs-module.md
│   ├── 202603261507_tts-engine.md
│   ├── 202603261508_mcp-server.md
│   └── 202603261509_e2e-testing.md
├── src/
│   └── tts_mcp/
│       ├── _logging.py          # setup_logging() for entry points
│       ├── cli.py               # Server entry point (argparse → wires everything)
│       ├── config.py            # Config dataclasses + load/validate
│       ├── audio.py             # AudioPlayer: sounddevice-based streaming playback
│       ├── engine.py            # TTSEngine: wires module + player, exposes speak()
│       ├── server.py            # MCP server: speak tool, StreamableHTTP
│       └── modules/
│           ├── __init__.py      # REGISTRY + load_module()
│           ├── base.py          # TTSModule ABC, TTSOptions, TTSError
│           └── elevenlabs.py    # ElevenLabs streaming module (MP3 → PCM via miniaudio)
├── tests/                       # Unit tests (fast, no external services) — collected by default
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_audio.py
│   ├── test_engine.py
│   ├── test_server.py
│   ├── test_project_map.py      # Drift-guard: project map ↔ code ↔ spec frontmatter
│   └── modules/
│       ├── test_tts_module_interface.py
│       └── test_elevenlabs.py
└── tests-e2e/                   # Opt-in e2e tests (hit real ElevenLabs API, require config.json + audio hw)
    ├── support.py               # require_env skip guard + subprocess/free-port/readiness helpers
    ├── conftest.py              # server_url fixture (starts subprocess, yields MCP URL)
    └── test_speak.py            # speak tool → ElevenLabs → AudioPlayer (no audio verification)
```

## Project map

Where each concept lives, and the spec that governs it. This table is the inverse of each spec's `code:` frontmatter, and a test (`tests/test_project_map.py`) enforces that it lists **exactly** the top-level `src/tts_mcp/*.py` concept modules — keep it in sync when you add, rename, or remove one.

| Module | Role | Spec |
|---|---|---|
| `src/tts_mcp/config.py` | Config dataclasses, `load_config()`, `ConfigError` | [configuration.md](specs/configuration.md) |
| `src/tts_mcp/audio.py` | `AudioPlayer` — sounddevice streaming playback | [audio-player.md](specs/audio-player.md) |
| `src/tts_mcp/engine.py` | `TTSEngine` — wires module + player, `speak()` | [architecture.md](specs/architecture.md) |
| `src/tts_mcp/server.py` | MCP server, `speak` tool, StreamableHTTP | [mcp-server.md](specs/mcp-server.md) |
| `src/tts_mcp/cli.py` | Entry point: argparse → config → engine → server | [project.md](specs/project.md) |
| `src/tts_mcp/_logging.py` | `setup_logging()` for entry points | [project.md](specs/project.md) |

The `modules/` subpackage (`base.py` — `TTSModule` ABC + `TTSOptions` + `TTSError`; `__init__.py` — `REGISTRY` + `load_module()`; `elevenlabs.py` — ElevenLabs streaming module) is governed by [tts-module-interface.md](specs/tts-module-interface.md) and [elevenlabs-module.md](specs/elevenlabs-module.md). The package's top-level `__init__.py` is package glue (exempt).

## Spec frontmatter

Every concept spec opens with a YAML frontmatter block naming the code and tests it governs — the **spec → code/tests** map, inverse of the Project map above:

```
---
code:
  - src/tts_mcp/config.py
tests:
  - tests/test_config.py
---
```

It gives the spec-drift checks an explicit, version-controlled scope. The mapping is many-to-many, so a file may appear in more than one spec. `tests/test_project_map.py` enforces three invariants: every listed path exists, every concept spec declares a non-empty `code:` list (project-wide overviews like [overview.md](specs/overview.md) are exempt), and every `src/tts_mcp/*.py` concept module is named by at least one spec. Keep the frontmatter current in the same change that moves or renames a file.

## Keeping statuses current

Specs and plans each carry a `**Status:**` line (near the top of the file, mirrored in the index), and you update it in the same change that does the work:

- **Spec status** — a lifecycle tracking design maturity *and* whether code matches: `Not started` → `Draft` (load-bearing open questions) → `Stable` (design settled, reviewed, validated — open questions are deferrals only — the design-review gate, **not necessarily implemented**) → `Implemented` (a `Done` plan built it and the code matches). Editing an `Implemented` spec in a way that needs new code flips it to `Updated` (code now lags) until a new plan closes the gap and returns it to `Implemented`. Purely editorial edits keep the status. Mirror every change in [specs/_index.md](specs/_index.md).
- **Plan status** — `Todo` → `In progress` → `Done`. Mark a plan `Done` only once it's implemented and verified (lint, type check, tests pass). Mirror in [plans/_index.md](plans/_index.md).

## Entry points

```bash
uv sync --dev                                # Materialize the environment
uv run tts-mcp-server --config config.json   # Start the MCP server
uv run pytest                                # Unit tests only (default tier — no API key needed)
uv run pytest tests-e2e/                     # Opt-in e2e tests (require config.json with valid API key)
uv run ruff check .                          # Lint
uv run pyright                               # Type-check
```

`testpaths = ["tests"]`, so the bare `uv run pytest` never touches the live tier — run `tests-e2e/` explicitly.

## Config structure

```json
{
  "tts": {
    "type": "elevenlabs",
    "api_key": "...",
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

`tts.type` selects the module; all other `tts` fields are module-specific. `audio.device` is `null` for the system default or a device name/index for explicit selection.

## Data flow

```
MCP client
  → speak tool call (text, voice?, ...)
    → TTSEngine.speak(text, options)
      → TTSModule.stream(text, options, callback=AudioPlayer.feed)
        → ElevenLabs API (streaming MP3) → miniaudio decode → PCM
          → AudioPlayer.feed(chunk) on each PCM chunk
            → sounddevice output stream
```

## Testing

Two physically-separated tiers — the full strategy (what a good test asserts, the speed budget, the smell checklist) is specced in [specs/testing.md](specs/testing.md):

- **`tests/`** — fast, in-process, no network; the default `uv run pytest` collects only this tier.
- **`tests-e2e/`** — opt-in; starts the server as a subprocess and drives the real ElevenLabs API + audio hardware. Skips cleanly when `config.json` is absent. It does **not** verify audio content — it asserts the `speak` call succeeds and audio bytes were produced.

## System dependencies

`sounddevice` requires PortAudio:

```bash
sudo apt-get install libportaudio2
```

## Verification

After any code change, run all three and fix any failures before considering the work done:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

Only mark a plan `Done` (and promote its spec to `Implemented`) once these pass.

## Logging conventions

- Every module uses `log = logging.getLogger(__name__)` (variable name: `log`, not `logger`).
- **Library modules** (`src/tts_mcp/`) never call `basicConfig` or configure handlers.
- **Entry points** (`cli.py`) call `setup_logging()` from `tts_mcp._logging` at startup.

## Adding a new TTS module

1. Create `src/tts_mcp/modules/<name>.py` implementing `TTSModule` from `modules/base.py`
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`
3. Document its config fields (the `tts` block accepts any fields beyond `type`)

## Documentation workflow

This project follows a two-layer, spec-driven convention:

1. **`specs/`** — Written before implementation. Describes *what* to build and *why*. Each opens with `code:`/`tests:` frontmatter and a `**Status:**` line (see "Spec frontmatter" and "Keeping statuses current").
2. **`plans/`** — Written before implementation. Describes *how* to build it, step by step. Each carries a `**Status:**` line and turns a settled part of a spec into buildable steps. Name each file `YYYYMMDDHHmm_kebab-title.md` (a date-time prefix, underscore, then a kebab-case title) so plans sort chronologically; start from [_plan-template.md](plans/_plan-template.md).

When implementing: work a plan's steps, keep its `**Status:**` (and the index row) current, and once verified promote the governing spec to `Implemented`. If you later edit an `Implemented` spec so the code no longer matches, set it to `Updated` and write a new plan to close the gap.

## Where to look first

- Understand the system: [`specs/_index.md`](specs/_index.md)
- Check implementation status: [`plans/_index.md`](plans/_index.md)
- Project structure & tooling: [`specs/project.md`](specs/project.md)
- Testing strategy: [`specs/testing.md`](specs/testing.md)
- Understand data flow: [`specs/architecture.md`](specs/architecture.md)
- Understand the module contract: [`specs/tts-module-interface.md`](specs/tts-module-interface.md)
