# Agent instructions

Start at [specs/_index.md](specs/_index.md) for what this project is, how its concepts fit together, and each spec's status ("Not started"/"Draft"/"Stable"/"Implemented"/"Updated"). For what has been (or is being) built, see [plans/_index.md](plans/_index.md), which lists each implementation plan and its status ("Todo"/"In progress"/"Done"). The specs are the source of truth for design; this file only says where things live and how to work here. Read the governing spec (its frontmatter names the files it covers) before changing code.

In one line: `tts-engine` is a streaming text-to-speech engine usable as a library (`TTSEngine`), as agent tools (`TTSTools`), or through an MCP server (the `mcp` extra) — the layering is in [specs/architecture.md](specs/architecture.md), the tooling and packaging decisions (extras, no default provider, logging split) in [specs/project.md](specs/project.md), and the config shape in [specs/configuration.md](specs/configuration.md) with one complete example per module under [examples/](examples/).

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/tts_engine/` | The library itself — one module per core concept (see below), plus the `modules/` backend subpackage |
| `examples/` | One complete config per module (`config.<type>.json`, no secrets, none the default) |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans (`YYYYMMDDHHmm_` prefixed) turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/tts_engine/` module structure — the only tier the default `pytest` collects |
| `tests-e2e/` | Opt-in tests against the real backends and audio hardware, not collected by default — strategy and skip rules in [specs/testing.md](specs/testing.md) |

### `src/tts_engine/` modules

| Module | Role | Spec |
|---|---|---|
| [config.py](src/tts_engine/config.py) | Config dataclasses (`TTSEngineConfig`, `MCPServerConfig`, each with `from_dict`/`from_json`/`from_json_file`), `ConfigError` | [configuration.md](specs/configuration.md) |
| [audio.py](src/tts_engine/audio.py) | `AudioSink` Protocol + `AudioPlayer`, the default sounddevice sink | [audio-player.md](specs/audio-player.md), [audio-sink.md](specs/audio-sink.md) |
| [engine.py](src/tts_engine/engine.py) | `TTSEngine` — builds module + sink from config, `say()` | [architecture.md](specs/architecture.md), [audio-sink.md](specs/audio-sink.md) |
| [tools.py](src/tts_engine/tools.py) | `TTSTools` — engine-bound, transport-agnostic tools (`say`) | [tools.md](specs/tools.md) |
| [mcp.py](src/tts_engine/mcp.py) | MCP server over StreamableHTTP, thin wrapper over the tools (behind the `mcp` extra) | [mcp-server.md](specs/mcp-server.md) |
| [mcp_server_cli.py](src/tts_engine/mcp_server_cli.py) | `tts-engine-mcp` entry point: args → config → engine → server; the one place logging is configured | [mcp-server.md](specs/mcp-server.md), [project.md](specs/project.md) |
| `modules/` | TTS backends: `base.py` (`TTSModule` ABC, `TTSOptions`, `TTSError`, `resolve_path`), `__init__.py` (`REGISTRY`, `load_module`), one file per backend — providers `elevenlabs.py`, `pocket.py`, `gradium.py` (each behind an extra) and fixtures `tone.py`, `audiofile.py` | [tts-module-interface.md](specs/tts-module-interface.md), plus one `<name>-module.md` spec per backend |
| `__init__.py` | Public API surface — re-exports `TTSEngine`, `TTSEngineConfig`, `MCPServerConfig`, `TTSTools`, `AudioSink`; package glue, exempt from the map check | — |

**Keep this map current:** when you add, rename, or remove a top-level `src/tts_engine/` module or a root directory, update the map in the same change — same discipline as keeping spec/plan statuses honest (below). `tests/test_project_map.py` enforces that every top-level `src/tts_engine/*.py` concept module appears here and vice-versa, and that the spec frontmatter (below) stays honest too.

## Keeping statuses current

Specs and plans each carry a `**Status:**` line near the top, mirrored in their `_index.md`; update both in the same change that does the work.

- **Spec status** tracks design maturity *and* whether code matches: `Not started` → `Draft` (load-bearing open questions) → `Stable` (design settled and reviewed — the design-review gate, **not** an implementation claim) → `Implemented` (a `Done` plan built it and the code matches). Editing an `Implemented` spec in a way that needs new code flips it to `Updated` (code now lags) until a new plan closes the gap and returns it to `Implemented`. Purely editorial edits keep the status.
- **Plan status**: `Todo` → `In progress` → `Done`. Mark a plan `Done` only once it is implemented and verified (lint, type check, tests pass — see Verification).

## Spec frontmatter

Every concept spec opens with a YAML frontmatter block naming the code and tests it governs — the **spec → code/tests** map, inverse of the module → spec column above:

```
---
code:
  - src/tts_engine/config.py
tests:
  - tests/test_config.py
---
```

It gives the spec-drift checks an explicit, version-controlled scope. The mapping is many-to-many, so a file may appear in more than one spec. `tests/test_project_map.py` enforces that every listed path exists, that every concept spec declares a non-empty `code:` list (project-wide overviews like [overview.md](specs/overview.md) are exempt), and that every `src/tts_engine/*.py` concept module is named by at least one spec. Keep the frontmatter current in the same change that moves or renames a file.

## Testing

Two physically separated tiers; what a good test asserts, the smell checklist and the 5-second budget for the unit tier are in [specs/testing.md](specs/testing.md) — read it before writing tests.

- `uv run pytest` runs only `tests/` (`testpaths = ["tests"]`): fast, no network, no credentials, no audio hardware.
- `uv run pytest tests-e2e/` is opt-in and drives the real backends and the machine's audio output. Provider rows skip cleanly when their key env var (e.g. `ELEVENLABS_API_KEY`) is unset or their extra is absent; the `tone`/`audiofile` fixture rows never skip.

**The keys live in `~/.zshrc`**, but the shell tool runs a non-interactive shell that doesn't source it — a plain `uv run pytest tests-e2e` there sees no keys and every provider case skips. Source it explicitly:

```bash
zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e'
```

Never `echo`/print a key itself; when checking whether one is set, redact the value (e.g. `env | grep ELEVENLABS_API_KEY | sed -E 's/=.*/=<set>/'`).

## Implementation plans

- Write plans as files in [plans/](plans/), starting from [_plan-template.md](plans/_plan-template.md); new specs start from [_spec-template.md](specs/_spec-template.md).
- Name each file `YYYYMMDDHHmm_kebab-title.md` (date-time prefix, underscore, kebab-case title) so plans sort chronologically, e.g. `202609171845_mcp-extra.md`.
- Give each a `**Status:**` line under its title and a row in [plans/_index.md](plans/_index.md).

## Adding a new TTS module

1. Create `src/tts_engine/modules/<name>.py` implementing `TTSModule` from `modules/base.py`. A provider goes behind its own packaging extra and imports its library lazily inside `__init__` ([project.md](specs/project.md), "Dependency strategy for TTS backends"); resolve file-path fields with `resolve_path`.
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`.
3. Write `specs/<name>-module.md` (config fields, audio format), add it to [specs/_index.md](specs/_index.md), and add `examples/config.<name>.json`.
4. Add a `pytest.param` row to the `MODULES` table in `tests-e2e/support.py` (plus a `_REQUIRED_IMPORT` entry if the backend sits behind an extra) so it gets live conformance coverage.

## Conventions

- Loggers are `log = logging.getLogger(__name__)` (the variable is `log`, not `logger`). Library code never configures handlers or touches the root logger; only the entry point calls `basicConfig` — the full split is in [project.md](specs/project.md), "Logging".

## Verification

After any code change, run linting, type checking, and tests, and fix any failures before considering the work done. Only mark a plan `Done` (and promote its spec to `Implemented`) once they pass.

## Commands

```bash
uv sync --dev                                # Full contributor environment (dev depends on tts-engine[all]: every extra, torch included)
uv sync --no-dev --extra pocket              # Consumer-style install (or --extra elevenlabs / --extra mcp); --no-dev is required, uv includes dev by default
uv run ruff check .
uv run ruff format .
uv run pyright
uv run pytest                                # Unit tier only
uv run pytest tests-e2e/                     # Opt-in live tier (see Testing for the key-sourcing invocation)
uv run tts-engine-mcp --config examples/config.tone.json   # Start the MCP server on the tone fixture
```
