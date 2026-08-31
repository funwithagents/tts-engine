---
code:
  - pyproject.toml
  - src/tts_engine/cli.py
  - src/tts_engine/_logging.py
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the `tts-engine` project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling. The runtime design lives in the other specs; this one is about how the project is built and checked.

## Decided

- **Identity:** `tts-engine` is a **library first** — a reusable `TTSEngine` — that also ships an MCP server as one interface onto it (see [overview.md](overview.md), [architecture.md](architecture.md)). The package is importable (`import tts_engine`) and the MCP is a console-script entry point.
- **Python version:** 3.11+ minimum (used for `str | None` unions, `tomllib`, match statements).
- **Package layout:** `src/` layout — `src/tts_engine/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`.
- **Runtime dependencies:** see the table below.
- **Linting/formatting:** `ruff`.
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`) that hits the real ElevenLabs API and audio hardware. Full strategy is specced in [testing.md](testing.md).
- **Distribution name:** `tts-engine` (`[project].name`).
- **Entry point:** `tts-engine-mcp = "tts_engine.cli:main"` (declared in `[project.scripts]`) — starts the MCP server. Named for the interface it launches, since the library itself is used by import, not by a script.
- **Public API:** `src/tts_engine/__init__.py` re-exports `TTSEngine`, `TTSEngineConfig`, and `load_config` (see [architecture.md](architecture.md), "Public API").
- **Repo shape:**
  - `src/tts_engine/` — the package, one module per core concept (`engine.py`, `tools.py`, `mcp.py`, `audio.py`, `config.py`, `cli.py`, `_logging.py`) plus the `modules/` subpackage of TTS backends.
  - `specs/` — pre-implementation design docs, one per concept (this folder), indexed by [_index.md](_index.md).
  - `plans/` — implementation plans turning settled specs into buildable steps, indexed by [_index.md](../plans/_index.md).
  - `tests/` at repo root, mirroring the `src/tts_engine/` module structure.
  - `tests-e2e/` at repo root, for the live tier above — not collected by the default `pytest` run.

## Entry point & plumbing

- `src/tts_engine/cli.py` — the `tts-engine-mcp` console script (`main`): parses `--config`, calls `load_config`, calls `setup_logging(cfg.logging.level)`, builds the engine via `TTSEngine.from_config(cfg.engine)`, creates the MCP server, and starts uvicorn.
- `src/tts_engine/_logging.py` — `setup_logging(level)` for entry points. It configures the **package** logger `logging.getLogger("tts_engine")` (attaches a handler and sets the level from config), never the root logger and never via `basicConfig`. Library modules never configure logging; only `cli.py` calls `setup_logging` (see [AGENTS.md](../AGENTS.md), "Logging conventions").

## Logging

- Every module uses `log = logging.getLogger(__name__)`. Under the `tts_engine` package these are children of the `tts_engine` logger, so they inherit its configured handler and level.
- The level is **not** hardcoded and the root logger is **not** touched: `setup_logging(level)` reads the level from the `logging` config block ([configuration.md](configuration.md)) and applies it to `logging.getLogger("tts_engine")`.

## Key dependencies

| Package | Purpose |
|---------|---------|
| `mcp[cli]` | MCP Python SDK (FastMCP, StreamableHTTP transport) |
| `uvicorn` | ASGI server for StreamableHTTP |
| `elevenlabs` | Official ElevenLabs Python SDK |
| `sounddevice` | PortAudio bindings for PCM playback |
| `numpy` | PCM byte→array conversion for sounddevice |
| `miniaudio` | Streaming MP3→PCM decode for the ElevenLabs module |

## System dependencies

`sounddevice` wraps PortAudio, which is a system library: `sudo apt-get install libportaudio2` on Ubuntu.

## Open questions

None currently.
