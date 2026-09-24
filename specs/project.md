---
code:
  - pyproject.toml
  - src/tts_engine/mcp_server_cli.py
tests:
  - tests/test_project_map.py
  - tests/modules/test_lazy_imports.py
  - tests/test_no_mcp_import.py
---

# Project

**Status:** Implemented

## Purpose

Structure and tooling for the `tts-engine` project itself: Python version, dependency/packaging management with `uv`, repo layout conventions, and development tooling. The runtime design lives in the other specs; this one is about how the project is built and checked.

## Decided

- **Identity:** `tts-engine` is a **library first** — a reusable `TTSEngine` — that also ships an MCP server as one interface onto it (see [overview.md](overview.md), [architecture.md](architecture.md)). The package is importable (`import tts_engine`) and the MCP is a console-script entry point. The base install carries only what the engine needs; each provider and each transport is an optional extra (see "Key dependencies").
- **Python version:** 3.11+ minimum (used for `str | None` unions, `tomllib`, match statements).
- **Package layout:** `src/` layout — `src/tts_engine/...` — not flat, to avoid accidentally importing an uninstalled package from the repo root.
- **Dependency/venv management:** `uv`. Dev tooling lives in the `dev` dependency group (`uv sync --dev`), not in runtime `dependencies`.
- **Runtime dependencies:** see the table below.
- **Linting/formatting:** `ruff`; `ruff check .` is the lint gate and `ruff format .` applies formatting (see [AGENTS.md](../AGENTS.md), "Commands").
- **Type checking:** `pyright` (`standard` mode), a dev dependency run via `uv run pyright`. Config lives in `[tool.pyright]` in `pyproject.toml`, targeting `src`, `tests`, and `tests-e2e`, pinned to the `.venv`.
- **Testing:** `pytest`, in two physically-separated tiers — a fast, deterministic, no-network default run (`tests/`, the only tier `testpaths` collects) and an opt-in live tier (`tests-e2e/`) that hits the real provider backends and audio hardware. Full strategy is specced in [testing.md](testing.md).
- **Distribution name:** `tts-engine` (`[project].name`).
- **Entry point:** `tts-engine-mcp = "tts_engine.mcp_server_cli:main"` (declared in `[project.scripts]`) — starts the MCP server. Extras cannot gate a console script, so it is installed with the base package, but it runs only with the `mcp` extra; without it the script exits with the install hint (see "Dependency strategy for transports"). Both the script and the module (`mcp_server_cli.py`) are named for the interface they launch, since the library itself is used by import, not by a script; the module name leaves room for other clients/entry points later. The MCP entry point is specced in [mcp-server.md](mcp-server.md).
- **Public API:** `src/tts_engine/__init__.py` re-exports `TTSEngine`, `TTSEngineConfig`, `MCPServerConfig`, `TTSTools`, and `AudioSink` (see [architecture.md](architecture.md), "Public API").
- **Repo shape:**
  - `src/tts_engine/` — the package, one module per core concept (`engine.py`, `tools.py`, `mcp.py`, `audio.py`, `config.py`, `mcp_server_cli.py`) plus the `modules/` subpackage of TTS backends.
  - `examples/` — one complete config file per module (`config.<type>.json`), so no module reads as the default; see [configuration.md](configuration.md).
  - `specs/` — pre-implementation design docs, one per concept (this folder), indexed by [_index.md](_index.md).
  - `plans/` — implementation plans turning settled specs into buildable steps, indexed by [_index.md](../plans/_index.md).
  - `tests/` at repo root, mirroring the `src/tts_engine/` module structure.
  - `tests-e2e/` at repo root, for the live tier above — not collected by the default `pytest` run.

## Entry point & plumbing

- `src/tts_engine/mcp_server_cli.py` — the `tts-engine-mcp` console script (`main`): parses `--config` and `--log-level` (default `INFO`), imports the MCP stack (`uvicorn`, `tts_engine.mcp`) inside `main` — exiting with the `pip install tts-engine[mcp]` hint when the `mcp` extra is absent — calls `MCPServerConfig.from_json_file`, configures logging via `logging.basicConfig(level=args.log_level, ...)`, builds the engine via `TTSEngine(cfg.engine)`, creates the MCP server, and starts uvicorn. Its runtime behaviour (transport, lifecycle) is specced in [mcp-server.md](mcp-server.md).

## Logging

The project follows the standard library-vs-application split:

- **Library side.** Every module that emits logs uses a module-level `log = logging.getLogger(__name__)`; under `tts_engine` these are children of the `tts_engine` logger. The package `__init__.py` attaches a `logging.NullHandler()` to `logging.getLogger("tts_engine")` and nothing else — so `import tts_engine` is silent and side-effect-free, and where records go is left entirely to the host application. The library never sets a level, adds a stream handler, calls `basicConfig`, or touches the root logger.
- **Application side.** The MCP entry point owns its process, so it configures logging the textbook way: `logging.basicConfig(level=..., format=...)` on the root logger. The level is an operational knob — it comes from the `tts-engine-mcp` `--log-level` flag (default `INFO`), not a config-file field, and there is no `logging` config block. The library's records reach root's handler by normal propagation (the `NullHandler` doesn't stop it). A pure library caller configures logging however its own application does.

## Key dependencies

The base install is the **engine only** — the engine, tools, config, audio player, and the two fixture modules (`tone`, `audiofile`). It is **provider-agnostic** (no real TTS backend is included, and none is the default) and **transport-agnostic** (no server stack is included). Every provider and every transport lives behind an extra (next sections).

| Package | Purpose |
|---------|---------|
| `sounddevice` | PortAudio bindings for PCM playback |
| `numpy` | PCM byte→array conversion for sounddevice; sine generation for the `tone` module |

Extras (`[project.optional-dependencies]`):

| Extra | Kind | Packages | Spec |
|---|---|---|---|
| `elevenlabs` | Provider | `elevenlabs` (official SDK), `miniaudio` (streaming MP3→PCM decode) | [elevenlabs-module.md](elevenlabs-module.md) |
| `pocket` | Provider | `pocket-tts` (pulls in `torch`) | [pocket-module.md](pocket-module.md) |
| `gradium` | Provider | `gradium` (official async SDK; pulls in `aiohttp`) | [gradium-module.md](gradium-module.md) |
| `mcp` | Transport | `mcp<2` (MCP Python SDK: FastMCP, StreamableHTTP transport), `uvicorn` (ASGI server) | [mcp-server.md](mcp-server.md) |
| `all` | — | `tts-engine[elevenlabs,pocket,gradium,mcp]` — every extra | — |

`mcp` is pinned below 2: mcp 2.x renamed `FastMCP` (`mcp.server.fastmcp` is gone), which `mcp.py` imports, and an unpinned fresh install resolves 2.x. It is the plain SDK, not `mcp[cli]`: the `cli` extra only adds `typer`/`python-dotenv` for the SDK's own `mcp dev`/`mcp install` tooling, which this project does not use. `uvicorn` is listed explicitly even though the SDK depends on it, because `mcp_server_cli.py` imports it directly.

The `dev` dependency group additionally depends on **`tts-engine[all]`** — the project itself with every extra, not a hand-maintained copy of the extras' members. So a plain `uv sync --dev` gives a contributor the whole suite: the unit tests that mock a real provider library (ElevenLabs), the MCP unit tests and live MCP test, and every live row in `tests-e2e/` with nothing skipped for a missing extra. Adding a provider extra to `all` reaches the dev environment for free. The cost is that every dev sync pulls `torch`; that weight is the *contributor's* to pay, not the consumer's, who still installs one provider at a time (see "Dependency strategy for TTS backends" below). The corollary: uv includes the `dev` group by default, so **`uv sync --no-dev`** is what a provider-minimal install in this repo takes — a bare `uv sync --extra <name>` materializes the full dev environment, torch included. Unit tests keep faking provider libraries regardless of what `dev` installs — a fast-tier test must assert behavior, not depend on a model download.

## Dependency strategy for TTS backends

Modules pull in third-party libraries of wildly different weight — the ElevenLabs SDK is a few MB of pure Python, while local-model backends (Kokoro, ChatTTS, …) pull in `torch` (~1–2 GB). Bundling backends into the base install would tax every user with dependencies they never load, and shipping one of them in the base would make it the de-facto default provider, which this project deliberately avoids. The decided approach:

- **One optional extra per provider, light or heavy.** Declared in `[project.optional-dependencies]` (PEP 621 extras), not `[dependency-groups]`: extras are installable by consumers of the published package (`pip install tts-engine[elevenlabs]` / `uv sync --extra pocket`), whereas dependency groups (like `dev`) are workflow-only and invisible downstream. The convenience `all` extra aggregates every provider.
- **The base is provider-agnostic; no provider is the default.** `pip install tts-engine` gives the framework and the two fixture modules (`tone`, `audiofile`, see [tts-module-interface.md](tts-module-interface.md), "Module kinds"), which need nothing beyond the base. To hear real speech a user picks a provider and installs its extra; `tts-engine-mcp` with a provider `type` whose extra is absent fails at startup with the `pip install tts-engine[<type>]` hint. Docs, examples, and tests treat every provider symmetrically — ElevenLabs is *an* API-backed provider, not the reference one.
- **Providers import their library lazily, never at module-file top.** `modules/__init__.py` eagerly imports each module *class* to populate the registry, so a top-level `import elevenlabs`/`import torch` would make `import tts_engine` (and `load_module`) require that library installed. Each provider therefore imports its dependency inside `__init__` (or first `stream()`), converting a missing extra into a clear `ConfigError`:

  ```python
  try:
      from kokoro import KPipeline
  except ImportError as exc:
      raise ConfigError(
          "The 'kokoro' module requires the kokoro extra: pip install tts-engine[kokoro]"
      ) from exc
  ```

  The registry stays static and `load_module` keeps importing fine; construction fails — with an actionable message — only when you actually select a backend whose extra isn't installed.
- **The library imports cleanly without any extra.** A subprocess guard test (`tests/modules/test_lazy_imports.py`) poisons `elevenlabs`, `miniaudio`, `pocket_tts`, `torch`, `gradium`, and `aiohttp` in `sys.modules`, then proves `import tts_engine` and the registry still load and that constructing each provider raises the `ConfigError` hint. Real-provider coverage lives in the opt-in `tests-e2e/` tier and skips cleanly when the extra or the key is absent (see [testing.md](testing.md)); the fixture modules give that tier something that always runs.

## Dependency strategy for transports

A transport is an interface onto the engine, not the engine itself (see [overview.md](overview.md)), and it brings its own server stack: the MCP SDK pulls in `pydantic`, `starlette`, `httpx`, `sse-starlette`, `jsonschema`, `pyjwt`, and `uvicorn`. A library caller or an agent embedding `TTSTools` needs none of that, and those packages (notably `pydantic` and `starlette`) often have to match versions the host application already pins. So transports follow the same rule as providers:

- **One optional extra per transport.** `mcp` today; a future transport gets its own (the HTTP server draft adds an `http` extra, see [_http-server.md](_http-server.md)). Installing the MCP server is `pip install tts-engine[mcp]` / `uv sync --extra mcp`; a deployment combines a transport with a provider, e.g. `tts-engine[mcp,elevenlabs]`.
- **Only the transport's own files import its stack.** `mcp.py` and `mcp_server_cli.py` are the only modules that import `mcp` or `uvicorn`; nothing on the `import tts_engine` path reaches them. Transport *configs* stay in the base: `MCPServerConfig` is a plain dataclass in `config.py`, so it stays importable and re-exported from `tts_engine` without the extra — a library caller can still read the engine block out of an MCP config file.
- **`mcp.py` imports its SDK at the top.** It is the transport itself, reachable only by explicit submodule import (`tts_engine.mcp` is not re-exported), so a plain `ModuleNotFoundError` is acceptable there.
- **The console script owns the install hint.** `tts-engine-mcp` is always installed, so `mcp_server_cli.py` keeps no top-level `mcp`/`uvicorn` import. `main` parses arguments first (so `--help` and a missing `--config` behave normally), then imports `uvicorn` and `tts_engine.mcp.create_server`. A `ModuleNotFoundError` whose missing module is exactly `mcp` or `uvicorn` (`exc.name in {"mcp", "uvicorn"}`) becomes `SystemExit("tts-engine-mcp requires the mcp extra: pip install tts-engine[mcp]")` — exit status 1, message on stderr, no traceback. Any other import error is re-raised unchanged, including a missing *submodule* such as `mcp.server.fastmcp` (an incompatible `mcp` version, not a missing extra) — so neither a real bug inside `tts_engine.mcp` nor a version mismatch is mislabelled as a missing extra.
- **Guarded by a subprocess test.** `tests/test_no_mcp_import.py` (alongside `tests/test_no_audio_import.py`) poisons `mcp` and `uvicorn` in `sys.modules`, then proves that `import tts_engine`, `MCPServerConfig.from_dict`, and `TTSTools(TTSEngine(config, sink=...)).say(...)` on the `tone` module all work, and that `tts_engine.mcp_server_cli.main()` exits with the `tts-engine[mcp]` hint.

## System dependencies

`sounddevice` wraps PortAudio, which is a system library: `sudo apt-get install libportaudio2` on Ubuntu.

## Open questions

None currently.
