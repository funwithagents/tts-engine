---
code:
  - pyproject.toml
  - src/tts_mcp/cli.py
  - src/tts_mcp/_logging.py
tests:
  - tests/test_project_map.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the `tts-mcp` project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling. The runtime design lives in the other specs; this one is about how the project is built and checked.

## Decided

- **Python version:** 3.11+ minimum (used for `str | None` unions, `tomllib`, match statements).
- **Package layout:** `src/` layout — `src/tts_mcp/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`.
- **Runtime dependencies:** see the table below.
- **Linting/formatting:** `ruff`.
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`) that hits the real ElevenLabs API and audio hardware. Full strategy is specced in [testing.md](testing.md).
- **Entry point:** `tts-mcp-server = "tts_mcp.cli:main"` (declared in `[project.scripts]`).
- **Repo shape:**
  - `src/tts_mcp/` — the package, one module per core concept (plus the `modules/` subpackage of TTS backends).
  - `specs/` — pre-implementation design docs, one per concept (this folder), indexed by [_index.md](_index.md).
  - `plans/` — implementation plans turning settled specs into buildable steps, indexed by [_index.md](../plans/_index.md).
  - `tests/` at repo root, mirroring the `src/tts_mcp/` module structure.
  - `tests-e2e/` at repo root, for the live tier above — not collected by the default `pytest` run.

## Entry point & plumbing

- `src/tts_mcp/cli.py` — the `tts-mcp-server` console script (`main`): parses `--config`, loads config, wires `TTSEngine` + `AudioPlayer`, and starts the server. It calls `setup_logging()` at startup.
- `src/tts_mcp/_logging.py` — `setup_logging()` for entry points. Library modules never configure logging; only `cli.py` does (see [AGENTS.md](../AGENTS.md), "Logging conventions").

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
