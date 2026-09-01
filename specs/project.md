---
code:
  - pyproject.toml
  - src/tts_engine/mcp_server_cli.py
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
- **Linting/formatting:** `ruff`; `ruff check .` is the lint gate and `ruff format .` applies formatting (see [AGENTS.md](../AGENTS.md), "Commands").
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`) that hits the real ElevenLabs API and audio hardware. Full strategy is specced in [testing.md](testing.md).
- **Distribution name:** `tts-engine` (`[project].name`).
- **Entry point:** `tts-engine-mcp = "tts_engine.mcp_server_cli:main"` (declared in `[project.scripts]`) — starts the MCP server. Both the script and the module (`mcp_server_cli.py`) are named for the interface they launch, since the library itself is used by import, not by a script; the module name leaves room for other clients/entry points later. The MCP entry point is specced in [mcp-server.md](mcp-server.md).
- **Public API:** `src/tts_engine/__init__.py` re-exports `TTSEngine`, `TTSEngineConfig`, and `load_config` (see [architecture.md](architecture.md), "Public API").
- **Repo shape:**
  - `src/tts_engine/` — the package, one module per core concept (`engine.py`, `tools.py`, `mcp.py`, `audio.py`, `config.py`, `mcp_server_cli.py`) plus the `modules/` subpackage of TTS backends.
  - `specs/` — pre-implementation design docs, one per concept (this folder), indexed by [_index.md](_index.md).
  - `plans/` — implementation plans turning settled specs into buildable steps, indexed by [_index.md](../plans/_index.md).
  - `tests/` at repo root, mirroring the `src/tts_engine/` module structure.
  - `tests-e2e/` at repo root, for the live tier above — not collected by the default `pytest` run.

## Entry point & plumbing

- `src/tts_engine/mcp_server_cli.py` — the `tts-engine-mcp` console script (`main`): parses `--config` and `--log-level` (default `INFO`), calls `load_config`, configures logging via `logging.basicConfig(level=args.log_level, ...)`, builds the engine via `TTSEngine(cfg.engine)`, creates the MCP server, and starts uvicorn. Its runtime behaviour (transport, lifecycle) is specced in [mcp-server.md](mcp-server.md).

## Logging

The project follows the standard library-vs-application split:

- **Library side.** Every module that emits logs uses a module-level `log = logging.getLogger(__name__)`; under `tts_engine` these are children of the `tts_engine` logger. The package `__init__.py` attaches a `logging.NullHandler()` to `logging.getLogger("tts_engine")` and nothing else — so `import tts_engine` is silent and side-effect-free, and where records go is left entirely to the host application. The library never sets a level, adds a stream handler, calls `basicConfig`, or touches the root logger.
- **Application side.** The MCP entry point owns its process, so it configures logging the textbook way: `logging.basicConfig(level=..., format=...)` on the root logger. The level is an operational knob — it comes from the `tts-engine-mcp` `--log-level` flag (default `INFO`), not a config-file field, and there is no `logging` config block. The library's records reach root's handler by normal propagation (the `NullHandler` doesn't stop it). A pure library caller configures logging however its own application does.

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
