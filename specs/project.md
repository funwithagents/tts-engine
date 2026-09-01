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

The base install is the **engine framework plus the reference (ElevenLabs) provider** — everything needed to run the MCP server and synthesize out of the box. Heavy/optional TTS backends are *not* here; they live behind extras (next section).

| Package | Purpose |
|---------|---------|
| `mcp[cli]` | MCP Python SDK (FastMCP, StreamableHTTP transport) |
| `uvicorn` | ASGI server for StreamableHTTP |
| `elevenlabs` | Official ElevenLabs Python SDK |
| `sounddevice` | PortAudio bindings for PCM playback |
| `numpy` | PCM byte→array conversion for sounddevice |
| `miniaudio` | Streaming MP3→PCM decode for the ElevenLabs module |

## Dependency strategy for TTS backends

Modules pull in third-party libraries of wildly different weight — the ElevenLabs SDK is a few MB of pure Python, while local-model backends (Kokoro, ChatTTS, …) pull in `torch` (~1–2 GB). Bundling every backend into the base install would tax every API-only and MCP user with dependencies they never load. The decided approach:

- **One optional extra per heavy backend.** Declared in `[project.optional-dependencies]` (PEP 621 extras), not `[dependency-groups]`: extras are installable by consumers of the published package (`pip install tts-engine[kokoro]` / `uv sync --extra kokoro`), whereas dependency groups (like `dev`) are workflow-only and invisible downstream. A convenience `all` extra aggregates the backends.
- **The base stays framework + ElevenLabs.** ElevenLabs is light and is the reference provider, so it stays a core dependency and `tts-engine-mcp` synthesizes out of the box. Only backends heavier than the framework itself go behind an extra. (If the base ever needs to be provider-agnostic, ElevenLabs — and its `miniaudio` decode dep — would move into an `elevenlabs` extra; not done now.)
- **Backends import their library lazily, never at module-file top.** `modules/__init__.py` eagerly imports each module *class* to populate the registry, so a top-level `import torch`/`import kokoro` would make `import tts_engine` (and `load_module`) require that library installed. Each optional backend therefore imports its heavy dependency inside `__init__` (or first `stream()`), converting a missing extra into a clear `ConfigError`:

  ```python
  try:
      from kokoro import KPipeline
  except ImportError as exc:
      raise ConfigError(
          "The 'kokoro' module requires the kokoro extra: pip install tts-engine[kokoro]"
      ) from exc
  ```

  The registry stays static and `load_module` keeps importing fine; construction fails — with an actionable message — only when you actually select a backend whose extra isn't installed.
- **The default test tier stays installable without any backend extra.** Because backend files import lazily, `tests/` can unit-test a backend with its library faked, never pulling `torch`. Real-backend coverage lives in the opt-in `tests-e2e/` tier and skips cleanly when the extra (or model) is absent — the same availability-gated pattern as the `ELEVENLABS_API_KEY` skips (see [testing.md](testing.md)).

## System dependencies

`sounddevice` wraps PortAudio, which is a system library: `sudo apt-get install libportaudio2` on Ubuntu.

## Open questions

None currently.
