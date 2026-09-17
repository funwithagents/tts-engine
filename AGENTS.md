# AGENTS.md — TTS Engine Project

## What this project is

A streaming text-to-speech **engine**, usable two ways: imported directly as a Python library (`TTSEngine(cfg.engine)` → `await engine.say(text)`), or run as an **MCP server** that exposes a `say` tool. It accepts text input, synthesizes speech through a pluggable TTS module (a provider installed as an extra — ElevenLabs or pocket-tts — or a built-in fixture module for tests), and plays the audio in real-time on the machine it runs on using streaming playback. The repo is three layers — the reusable `TTSEngine`, provider-agnostic **tools** over it, and the **MCP** that exposes those tools — so the MCP is one interface, not the product.

- **Language**: Python, project managed with `uv`
- **MCP SDK**: official Python SDK (`modelcontextprotocol/python-sdk`)
- **Transport**: StreamableHTTP — enables remote access on a local network
- **One active module**: a single TTS module is loaded at startup, selected via the module `type` in config

## Key design decisions

- **Streaming playback**: audio is streamed from the TTS provider and fed to the audio device chunk-by-chunk, minimising latency before sound starts.
- **Callback-based streaming**: the module layer accepts a `callback: Callable[[bytes], None]` for each audio chunk — this decouples the module from the playback mechanism and makes the engine testable without audio hardware.
- **MP3 from ElevenLabs, decoded in-process**: the ElevenLabs module requests `mp3_44100_128` and decodes each chunk to signed-16 PCM mono via `miniaudio` before the callback, so `AudioPlayer` always receives PCM.
- **No default provider**: the base install is provider-agnostic; every provider is an extra (`elevenlabs`, `pocket`, `all`) whose library is imported lazily in the module's `__init__` (a missing extra → `ConfigError` with a `pip install tts-engine[<name>]` hint); `tone` and `audiofile` are fixture modules for tests and demos, never presented as TTS.
- **`base_dir` for relative paths**: `base_dir` is a reserved `engine.module` key — the directory relative file paths resolve against. `from_json_file` fills it with the config file's directory (`from_dict`/`from_json` take a `base_dir=` keyword); modules read it only through `resolve_path(config, value)` in `modules/base.py`.
- **`say` tool only (v1)**: no `synthesize`/file output, no `list_voices`, no MCP resources.
- **Pluggable modules**: the `engine.module` config block uses `type` to select the module; all other fields under `engine.module` are module-specific. Only one module is active at a time.
- **`sounddevice` for playback**: wraps PortAudio, best choice on Ubuntu; device is configurable via `engine.player.device` (`null` = system default).

## Project map

Where things live. This is a coarse, module-level map — for the full file inventory use `git ls-files`; for design detail follow the spec links.

### Top-level layout

| Path | What's there |
|---|---|
| `src/tts_engine/` | The library itself — one module per core concept (see below), plus the `modules/` provider subpackage |
| `examples/` | One complete config per module (`config.<type>.json`, no secrets, none the default) — see [configuration.md](specs/configuration.md) |
| `specs/` | Pre-implementation design docs, one per concept, each with a `**Status:**` — indexed by [specs/_index.md](specs/_index.md) |
| `plans/` | Implementation plans (`YYYYMMDDHHmm_` prefixed) turning settled specs into buildable steps — indexed by [plans/_index.md](plans/_index.md) |
| `tests/` | Fast, deterministic, no-network tests; mirrors the `src/tts_engine/` module structure — collected by default `pytest` |
| `tests-e2e/` | Opt-in full-loop tests hitting the real TTS backends (ElevenLabs API, local pocket-tts model) + audio hardware (not collected by default `pytest`); each provider case skips cleanly when its key env var (e.g. `ELEVENLABS_API_KEY`) is unset or its packaging extra is absent, while the fixture-module rows (`tone`, `audiofile`, the latter using the committed WAVs in `tests-e2e/fixtures/`) never skip |

### `src/tts_engine/` modules

| Module | Role | Spec |
|---|---|---|
| [config.py](src/tts_engine/config.py) | Config dataclasses (`TTSEngineConfig`, `MCPServerConfig`, each with a `from_dict`/`from_json`/`from_json_file` trio), `ConfigError` | [configuration.md](specs/configuration.md) |
| [audio.py](src/tts_engine/audio.py) | `AudioSink` Protocol + `AudioPlayer` — sounddevice streaming playback (default sink) | [audio-player.md](specs/audio-player.md), [audio-sink.md](specs/audio-sink.md) |
| [engine.py](src/tts_engine/engine.py) | `TTSEngine` — builds module + sink (default player) from `TTSEngineConfig`, `say()`, `sample_rate` | [architecture.md](specs/architecture.md), [audio-sink.md](specs/audio-sink.md) |
| [tools.py](src/tts_engine/tools.py) | `TTSTools` — engine-bound, provider/transport-agnostic tools (`say`) | [tools.md](specs/tools.md) |
| [mcp.py](src/tts_engine/mcp.py) | MCP server, `say` tool (thin wrapper over tools), StreamableHTTP | [mcp-server.md](specs/mcp-server.md) |
| [mcp_server_cli.py](src/tts_engine/mcp_server_cli.py) | MCP server entry point: argparse → config → engine → MCP server; configures logging via `basicConfig` | [mcp-server.md](specs/mcp-server.md) |
| `modules/` | Module subpackage: `base.py` (`TTSModule` ABC + `TTSOptions` + `TTSError` + `resolve_path`), `__init__.py` (`REGISTRY` + `load_module()`), `elevenlabs.py` (ElevenLabs streaming provider behind the `elevenlabs` extra, MP3 → PCM), `pocket.py` (local-model pocket-tts provider behind the `pocket` extra, float → PCM), `tone.py` (fixture: sine tone), `audiofile.py` (fixture: text → WAV files) | [tts-module-interface.md](specs/tts-module-interface.md), [elevenlabs-module.md](specs/elevenlabs-module.md), [pocket-module.md](specs/pocket-module.md), [tone-module.md](specs/tone-module.md), [audiofile-module.md](specs/audiofile-module.md) |
| `__init__.py` | Public API surface — re-exports `TTSEngine`, `TTSEngineConfig`, `MCPServerConfig`, `TTSTools`, `AudioSink`; package glue, exempt from the map check | — |

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
uv sync --dev                                # Materialize the environment (no provider extra)
uv sync --dev --extra elevenlabs             #   … plus one provider (or --extra pocket)
uv sync --dev --all-extras                   #   … plus every provider
uv run tts-engine-mcp --config examples/config.tone.json   # Start the MCP server
uv run pytest                                # Unit tests only (default tier — no API key needed)
ELEVENLABS_API_KEY=sk_... uv run pytest tests-e2e/   # Opt-in e2e tests (skip unless the key is set)
uv run ruff check .                          # Lint
uv run pyright                               # Type-check
```

`testpaths = ["tests"]`, so the bare `uv run pytest` never touches the live tier — run `tests-e2e/` explicitly.

## Config structure

One provider example (ElevenLabs); [examples/](examples/) holds a complete config for every module, and none is the default:

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
  }
}
```

`MCPServerConfig.from_json_file(path)` parses this file into an `MCPServerConfig(engine, server)`; `TTSEngineConfig.from_dict(engine_block)` (or `from_json`/`from_json_file`) builds just the `engine` block for library callers. `engine` builds the `TTSEngine` (`TTSEngineConfig` = module + player); `engine.module.type` selects the module and all other `engine.module` fields are module-specific; `engine.player.device` is `null` for the system default or a device name/index. `server` is used only by the MCP entry point. `engine` is required; `server` defaults when omitted. `engine.module.base_dir` is reserved: relative file paths in the module block resolve against it, and the file loaders fill it with the config file's directory. There is no `logging` block — the log level comes from the MCP server's `--log-level` flag (default `INFO`), not the config file.

## Data flow

```
MCP client                              Library caller / agent
  → say tool call (text)                  → TTSTools(engine).say(text)  ─┐
    → mcp.py say wrapper                                                  │
      → TTSTools.say(text)  ◀────────────────────────────────────────────┘
        → TTSEngine.say(text)
          → TTSModule.stream(text, options, callback=sink.feed)
            → elevenlabs: ElevenLabs API (streaming MP3) → miniaudio decode → PCM
              pocket:     local pocket-tts inference (float32) → int16 PCM
              tone:       sine wave (fixture)
              audiofile:  WAV file read (fixture)
              → sink.feed(chunk) on each PCM chunk
                → AudioPlayer (default sink) → sounddevice output stream
                   or an injected AudioSink (capture / custom destination)
```

## Testing

Two physically-separated tiers — the full strategy (what a good test asserts, the speed budget, the smell checklist) is specced in [specs/testing.md](specs/testing.md):

- **`tests/`** — fast, in-process, no network; the default `uv run pytest` collects only this tier.
- **`tests-e2e/`** — opt-in; drives the real provider backends (ElevenLabs API, local pocket-tts) + audio hardware, plus the `tone`/`audiofile` fixture modules. Module configs are hardcoded in `tests-e2e/support.py` (no committed `config.json`); a provider config carries `api_key_env` (naming `ELEVENLABS_API_KEY`), never a key, so a case skips cleanly when its env var is unset or its extra's library is absent via `support.require_module` (`_REQUIRED_IMPORT` has one row per provider extra; fixture modules declare nothing). It does **not** verify audio content. `test_modules.py` runs two scenarios per backend over the `MODULES` table — PCM conformance into a capture sink, and `say` completing through real audio hardware at the module's declared rate; `test_mcp.py` covers the module-agnostic `say` tool over StreamableHTTP against a subprocess server driving the default module, the `tone` fixture, so it never skips.

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
- **Library modules** (`src/tts_engine/`) never call `basicConfig` or configure handlers, and never touch the root logger. The package `__init__.py` attaches a `NullHandler` to the `tts_engine` logger (and nothing else), so `import tts_engine` is silent and where records go is left to the host application.
- **Entry points** (`mcp_server_cli.py`) own their process and configure logging the textbook way: `logging.basicConfig(level=..., format=...)` at startup. The level comes from the `--log-level` flag (default `INFO`), not from config.

## Adding a new TTS module

1. Create `src/tts_engine/modules/<name>.py` implementing `TTSModule` from `modules/base.py`. A provider goes behind its own packaging extra and imports its library lazily inside `__init__` (see [project.md](specs/project.md), "Dependency strategy for TTS backends"); resolve file-path fields with `resolve_path`
2. Register it in `modules/__init__.py`: `REGISTRY["<name>"] = <ClassName>`
3. Document its config fields (the `engine.module` block accepts any fields beyond `type`) in a `specs/<name>-module.md` spec, add the spec to the `modules/` row of the project map above, and add an `examples/config.<name>.json`
4. Add a `pytest.param` row to the `MODULES` table in `tests-e2e/support.py` (plus a `_REQUIRED_IMPORT` entry if the backend sits behind a packaging extra) so it gets live conformance coverage

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
