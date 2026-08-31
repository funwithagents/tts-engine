# TTS MCP — Specifications Index

`tts-mcp` is an MCP server that exposes text-to-speech as a single `speak` tool: an MCP client sends text, the server synthesizes it through a pluggable TTS backend (ElevenLabs first) and plays the audio in real-time on the server machine. The core idea is **streaming, low-latency playback** — audio is fed to the sound device chunk-by-chunk as it arrives from the provider, so sound starts before synthesis finishes — behind a clean module contract that keeps the provider swappable and the engine testable without audio hardware.

Each spec opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs (the spec → code/tests map the drift checks use); see [AGENTS.md](../AGENTS.md) ("Spec frontmatter"). New specs start from [_spec-template.md](_spec-template.md).

## Specs

Read the concept specs in order — each builds on the ones above it. `project.md` and `testing.md` are project-wide (tooling and test strategy) and can be read any time.

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, ruff/pyright, layout | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split, functional-test philosophy, skip-without-credentials live tier | Implemented |
| [overview.md](overview.md) | Goals, components, constraints, non-goals | Stable |
| [architecture.md](architecture.md) | System diagram, concurrency model, data flow, `TTSEngine` | Implemented |
| [configuration.md](configuration.md) | Config file schema, fields, validation rules | Implemented |
| [mcp-server.md](mcp-server.md) | `speak` tool, transport, lifecycle, error handling | Implemented |
| [tts-module-interface.md](tts-module-interface.md) | ABC, audio format contract, registry, `TTSOptions` | Implemented |
| [elevenlabs-module.md](elevenlabs-module.md) | Streaming PCM, config fields, SDK usage, error handling | Implemented |
| [audio-player.md](audio-player.md) | `AudioPlayer`, sounddevice integration, stream lifecycle | Implemented |

## Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being defined; contains load-bearing open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), ready to implement but not necessarily implemented yet. The design-review gate, before code.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches; a new plan is needed (or in progress). Returns to **Implemented** once that plan is `Done`.
