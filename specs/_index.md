# TTS Engine — Specifications Index

`tts-engine` is a streaming text-to-speech **engine** you can use two ways: import it directly as a Python library (`TTSEngine(cfg.engine)` → `await engine.speak(text)`), or run it as an **MCP server** that exposes a `speak` tool. The engine synthesizes text through a pluggable TTS backend (ElevenLabs first) and plays the audio in real-time on the machine it runs on. The core idea is **streaming, low-latency playback** — audio is fed to the sound device chunk-by-chunk as it arrives from the provider, so sound starts before synthesis finishes — behind a clean module contract that keeps the provider swappable and the engine testable without audio hardware.

The repo is organized as three layers: the **`TTSEngine`** (the reusable core), a set of provider-agnostic **tools** (`speak(engine, text)`), and the **MCP** server that exposes those tools. The MCP is one interface onto the engine, not the product.

Each spec opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs (the spec → code/tests map the drift checks use); see [AGENTS.md](../AGENTS.md) ("Spec frontmatter"). New specs start from [_spec-template.md](_spec-template.md).

## Specs

Read the concept specs in order — each builds on the ones above it. `project.md` and `testing.md` are project-wide (tooling and test strategy) and can be read any time.

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, ruff/pyright, layout, library-first shape | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split, functional-test philosophy, skip-without-credentials live tier | Implemented |
| [overview.md](overview.md) | Goals, components, constraints, non-goals — library + MCP framing | Stable |
| [architecture.md](architecture.md) | System diagram, layers (engine/tools/mcp), concurrency model, data flow, `TTSEngine(config)` construction, public API | Implemented |
| [configuration.md](configuration.md) | Config file schema (`engine`/`server`/`logging`), dataclasses, `load_config`, validation rules | Implemented |
| [tools.md](tools.md) | The provider-agnostic tools layer: `speak(engine, text)`, guards, return contract | Implemented |
| [mcp-server.md](mcp-server.md) | `speak` tool, transport, lifecycle, error handling — thin wrappers over the tools layer | Implemented |
| [tts-module-interface.md](tts-module-interface.md) | ABC, audio format contract, registry, `TTSOptions` | Implemented |
| [elevenlabs-module.md](elevenlabs-module.md) | Streaming PCM, config fields, SDK usage, error handling | Implemented |
| [audio-player.md](audio-player.md) | `AudioPlayer`, sounddevice integration, stream lifecycle | Implemented |

## Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being defined; contains load-bearing open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), ready to implement but not necessarily implemented yet. The design-review gate, before code.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches; a new plan is needed (or in progress). Returns to **Implemented** once that plan is `Done`.
