# AGENTS.md — TTS Engine Project

## What this project is

A streaming text-to-speech **engine**, usable two ways: imported directly as a Python library (`TTSEngine(cfg.engine)` → `await engine.speak(text)`), or run as an **MCP server** that exposes a `speak` tool. It accepts text input, synthesizes speech through a pluggable TTS module (ElevenLabs first), and plays the audio in real-time on the machine it runs on using streaming playback. The repo is three layers — the reusable `TTSEngine`, provider-agnostic **tools** over it, and the **MCP** that exposes those tools — so the MCP is one interface, not the product.

- **Language**: Python, project managed with `uv`
- **MCP SDK**: official Python SDK (`modelcontextprotocol/python-sdk`)
- **Transport**: StreamableHTTP — enables remote access on a local network
- **One active module**: a single TTS module is loaded at startup, selected via the module `type` in config

## Key design decisions

- **Streaming playback**: audio is streamed from the TTS provider and fed to the audio device chunk-by-chunk, minimising latency before sound starts.
- **Callback-based streaming**: the module layer accepts a `callback: Callable[[bytes], None]` for each audio chunk — this decouples the module from the playback mechanism and makes the engine testable without audio hardware.
- **MP3 from ElevenLabs, decoded in-process**: the ElevenLabs module requests `mp3_44100_128` and decodes each chunk to signed-16 PCM mono via `miniaudio` before the callback, so `AudioPlayer` always receives PCM.
- **`speak` tool only (v1)**: no `synthesize`/file output, no `list_voices`, no MCP resources.
- **Pluggable modules**: the `engine.module` config block uses `type` to select the module; all other fields under `engine.module` are module-specific. Only one module is active at a time.
- **`sounddevice` for playback**: wraps PortAudio, best choice on Ubuntu; device is configurable via `engine.player.device` (`null` = system default).

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/tts_engine/` | The library itself — one module per core concept (see below), plus the `modules/` provider subpackage |
| `config.example.json` | Config template (no secrets) — see [configuration.md](specs/configuration.md) |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans (`YYYYMMDDHHmm_` prefixed) turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/tts_engine/` module structure — collected by default `pytest` |
| `tests-e2e/` | Opt-in full-loop tests hitting the real ElevenLabs API + audio hardware (not collected by default `pytest`); skip cleanly when `ELEVENLABS_API_KEY` is unset |

### `src/tts_engine/` modules

| Module | Role | Spec |
|---|---|---|
| [config.py](src/tts_engine/config.py) | Config dataclasses, `load_config()`, `ConfigError` | [configuration.md](specs/configuration.md) |
| [audio.py](src/tts_engine/audio.py) | `AudioPlayer` — sounddevice streaming playback | [audio-player.md](specs/audio-player.md) |
| [engine.py](src/tts_engine/engine.py) | `TTSEngine` — builds module + player from `TTSEngineConfig`, `speak()` | [architecture.md](specs/architecture.md) |
| [tools.py](src/tts_engine/tools.py) | Provider/transport-agnostic tools over an engine (`speak`) | [tools.md](specs/tools.md) |
| [mcp.py](src/tts_engine/mcp.py) | MCP server, `speak` tool (thin wrapper over tools), StreamableHTTP | [mcp-server.md](specs/mcp-server.md) |
| [mcp_server_cli.py](src/tts_engine/mcp_server_cli.py) | MCP server entry point: argparse → config → engine → MCP server | [mcp-server.md](specs/mcp-server.md) |
| [_logging.py](src/tts_engine/_logging.py) | `setup_logging(level)` — configures the package logger for entry points | [project.md](specs/project.md) |
| `modules/` | Provider subpackage: `base.py` (`TTSModule` ABC + `TTSOptions` + `TTSError`), `__init__.py` (`REGISTRY` + `load_module()`), `elevenlabs.py` (ElevenLabs streaming module, MP3 → PCM) | [tts-module-interface.md](specs/tts-module-interface.md), [elevenlabs-module.md](specs/elevenlabs-module.md) |
| `__init__.py` | Public API surface — re-exports `TTSEngine`, `TTSEngineConfig`, `load_config`; package glue, exempt from the map check | — |

**Keep this map current:** when you add, rename, or remove a top-level `src/tts_engine/` module or a root directory, update the map in the same change — same discipline as keeping spec/plan statuses honest (below). A test (`tests/test_project_map.py`) enforces that every top-level `src/tts_engine/*.py` concept module appears here and vice-versa — and that the spec frontmatter (see below) stays honest too.

## Spec frontmatter

Every concept spec opens with a YAML frontmatter block naming the code and tests it governs — the **spec → code/tests** map, inverse of the Project map above:

```
---
code:
  - src/tts_engine/config.py
tests:
  - tests/test_config.py
---
```

It gives the spec-drift checks an explicit, version-controlled scope. The mapping is many-to-many, so a file may appear in more than one spec. `tests/test_project_map.py` enforces three invariants: every listed path exists, every concept spec declares a non-empty `code:` list (project-wide overviews like [overview.md](specs/overview.md) are exempt), and every `src/tts_engine/*.py` concept module is named by at least one spec. Keep the frontmatter current in the same change that moves or renames a file.

## Keeping statuses current

Specs and plans each carry a `**Status:**` line (near the top of the file, mirrored in the index), and you update it in the same change that does the work:

- **Spec status** — a lifecycle tracking design maturity *and* whether code matches: `Not started` → `Draft` (load-bearing open questions) → `Stable` (design settled, reviewed, validated — open questions are deferrals only — the design-review gate, **not necessarily implemented**) → `Implemented` (a `Done` plan built it and the code matches). Editing an `Implemented` spec in a way that needs new code flips it to `Updated` (code now lags) until a new plan closes the gap and returns it to `Implemented`. Purely editorial edits keep the status. Mirror every change in [specs/_index.md](specs/_index.md).
- **Plan status** — `Todo` → `In progress` → `Done`. Mark a plan `Done` only once it's implemented and verified (lint, type check, tests pass). Mirror in [plans/_index.md](plans/_index.md).

## Entry points

```bash
uv sync --dev                                # Materialize the environment
uv run tts-engine-mcp --config config.json   # Start the MCP server
uv run pytest                                # Unit tests only (default tier — no API key needed)
ELEVENLABS_API_KEY=sk_... uv run pytest tests-e2e/   # Opt-in e2e tests (skip unless the key is set)
uv run ruff check .                          # Lint
uv run pyright                               # Type-check
```

`testpaths = ["tests"]`, so the bare `uv run pytest` never touches the live tier — run `tests-e2e/` explicitly.

## Config structure

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

`load_config()` returns an `AppConfig(engine, server, logging)`. `engine` builds the `TTSEngine` (`TTSEngineConfig` = module + player); `engine.module.type` selects the module and all other `engine.module` fields are module-specific; `engine.player.device` is `null` for the system default or a device name/index. `server` is used only by the MCP entry point; `logging.level` sets the `tts_engine` package logger's level. `engine` is required; `server` and `logging` default when omitted.

## Data flow

```
MCP client                              Library caller
  → speak tool call (text)                → tools.speak(engine, text)  ─┐
    → mcp.py speak wrapper                                              │
      → tools.speak(engine, text)  ◀──────────────────────────────────-┘
        → TTSEngine.speak(text)
          → TTSModule.stream(text, options, callback=AudioPlayer.feed)
            → ElevenLabs API (streaming MP3) → miniaudio decode → PCM
              → AudioPlayer.feed(chunk) on each PCM chunk
                → sounddevice output stream
```

## Testing

Two physically-separated tiers — the full strategy (what a good test asserts, the speed budget, the smell checklist) is specced in [specs/testing.md](specs/testing.md):

- **`tests/`** — fast, in-process, no network; the default `uv run pytest` collects only this tier.
- **`tests-e2e/`** — opt-in; drives the real ElevenLabs API + audio hardware, from the committed secret-free `tests-e2e/config.json` (its `api_key_env` names `ELEVENLABS_API_KEY`). Skips cleanly when that env var is unset. It does **not** verify audio content — it asserts the `speak` call completes. Two scenarios, one file each: `test_engine.py` (in-process library path, `TTSEngine(cfg.engine) → speak`) and `test_mcp.py` (`speak` tool over StreamableHTTP against a subprocess server).

**The keys live in `~/.zshrc`**, but the shell tool runs a non-interactive `bash`/`zsh` that doesn't source it — a plain `uv run pytest tests-e2e` in that shell sees no keys and every case skips. Source it explicitly in an interactive `zsh` invocation:

```bash
zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e'
```

Never `echo`/print a key itself; when checking whether one is set, redact the value (e.g. `env | grep ELEVENLABS_API_KEY | sed -E 's/=.*/=<set>/'`).

## System dependencies

`sounddevice` requires PortAudio:

```bash
sudo apt-get install libportaudio2
```

## Verification

After any code change, run linting, type checking, and tests, and fix any failures before considering the work done.

## Commands

```
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest
```

Only mark a plan `Done` (and promote its spec to `Implemented`) once these pass.

## Logging conventions

- Every module that emits logs uses `log = logging.getLogger(__name__)` (variable name: `log`, not `logger`).
- **Library modules** (`src/tts_engine/`) never call `basicConfig` or configure handlers, and never touch the root logger.
- **Entry points** (`mcp_server_cli.py`) call `setup_logging(level)` from `tts_engine._logging` at startup — it configures the package logger (`tts_engine`), not root. The level comes from the `logging` config block.

## Adding a new TTS module

1. Create `src/tts_engine/modules/<name>.py` implementing `TTSModule` from `modules/base.py`
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`
3. Document its config fields (the `engine.module` block accepts any fields beyond `type`)

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
