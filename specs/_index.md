# TTS Engine — Specifications Index

`tts-engine` is a streaming text-to-speech **engine** you can use two ways: import it directly as a Python library (`TTSEngine(cfg.engine)` → `await engine.say(text)`), or run it as an **MCP server** that exposes a `say` tool. The engine synthesizes text through a pluggable TTS backend (a provider such as the ElevenLabs, Gradium, Mistral or Deepgram APIs or the local pocket-tts or Phonon models, each an optional extra with no default; or a built-in fixture module for tests) and plays the audio in real-time on the machine it runs on. The core idea is **streaming, low-latency playback** — audio is fed to the sound device chunk-by-chunk as it arrives from the provider, so sound starts before synthesis finishes — behind a clean module contract that keeps the provider swappable and the engine testable without audio hardware.

The repo is organized as three layers: the **`TTSEngine`** (the reusable core), the provider-agnostic **tools** (`TTSTools(engine).say(text)`), and the **MCP** server that exposes those tools. The MCP is one interface onto the engine, not the product.

Each spec opens with a YAML **frontmatter** block declaring the `code:` and `tests:` files it governs (the spec → code/tests map the drift checks use); see [AGENTS.md](../AGENTS.md) ("Spec frontmatter"). New specs start from [_spec-template.md](_spec-template.md).

Per-backend TTS module specs live in [`modules/`](modules/), one file per backend, mirroring `src/tts_engine/modules/<backend>/`. The contract they implement, [tts-module-interface.md](tts-module-interface.md), stays here.

## Specs

Read the concept specs in order — each builds on the ones above it. `project.md`, `testing.md` and `ci.md` are project-wide (tooling, test strategy, continuous integration) and can be read any time.

| Spec | Description | Status |
|---|---|---|
| [project.md](project.md) | Project structure and tooling: Python version, packaging with uv, ruff/pyright, layout, library-first shape, provider- and transport-agnostic base + one extra per provider and per transport (`mcp`), supported platforms (Linux x86_64/arm64, macOS, Windows; Intel macOS partial), CPU torch index on Linux | Implemented |
| [testing.md](testing.md) | Testing strategy: two-tier `tests/`/`tests-e2e/` split, functional-test philosophy, skip-without-credentials live tier, per-module live conformance (complete text and text stream), `tone` as the live default module | Implemented |
| [ci.md](ci.md) | Continuous integration on GitHub's hosted runners: static gate, fast tier on Linux x86_64/arm64, macOS and Windows, live tier on Linux against a PulseAudio null sink, optional provider-key secrets, expected skips | Implemented |
| [overview.md](overview.md) | Goals, components, constraints, non-goals — library + MCP framing | Stable |
| [architecture.md](architecture.md) | System diagram, layers (engine/tools/mcp), concurrency model, data flow for a complete text (`say`) and a text stream (`say_stream`), `TTSEngine(config)` construction, public API | Implemented |
| [configuration.md](configuration.md) | Config file schema (`engine`/`server`), dataclasses, `MCPServerConfig`/`TTSEngineConfig` constructor trio with `base_dir`, reserved `module.base_dir` key, validation rules | Implemented |
| [tools.md](tools.md) | The provider-agnostic tools layer: `TTSTools(engine).say(text)`, guards, return contract, agent-registrable bound methods | Implemented |
| [mcp-server.md](mcp-server.md) | `mcp` extra + install hint, `say` tool, transport, lifecycle, error handling — thin wrappers over the tools layer | Implemented |
| [tts-module-interface.md](tts-module-interface.md) | ABC with two input shapes (`synthesize_text`, `synthesize_text_stream`), audio format contract (module-declared sample rate), module kinds (provider / fixture), registry with no default, `resolve_path`, `TTSOptions`, text-stream helpers (`run_text_stream_worker`, `segment_sentences`) | Implemented |
| [elevenlabs-module.md](modules/elevenlabs.md) | API-backed provider: `elevenlabs` extra, lazy import, streaming MP3→PCM, native text stream over the stream-input WebSocket, config fields, error handling | Implemented |
| [pocket-module.md](modules/pocket.md) | Local-model provider (pocket-tts 4): `pocket` extra, lazy import, native 24 kHz, language/dated/tiny models or a custom `config`, per-language default voice, device auto-detect, CPU-only `quantize`, `temperature`, library `stop` event for cancellation, float→int16, sentence-grouped text stream, `base_dir` for a voice file | Implemented |
| [gradium-module.md](modules/gradium.md) | API-backed provider with an async SDK: `gradium` extra, lazy import, raw `pcm_<rate>` output (no decode), configurable sample rate, voice settings, private event loop in the cancellable worker, native text stream (sender + receiver on one socket) | Implemented |
| [phonon-module.md](modules/phonon.md) | Local-model provider on Gradium's `ptts` runtime (no torch): `phonon` extra, lazy import, native 24 kHz, `language` defaulting to `en`, catalog or `.safetensors` voice, `auto` device, CPU-only quantization, sentence grouping for the text stream; Apache-2.0 default checkpoint, CC BY-NC gated ones | Implemented |
| [mistral-module.md](modules/mistral.md) | API-backed provider on Mistral's Voxtral TTS: `mistral` extra, lazy import, streamed float32 `pcm` at 24 kHz → int16, fed in 100 ms slices for prompt cancellation, sentence-grouped text stream (one request per sentence) | Implemented |
| [deepgram-module.md](modules/deepgram.md) | API-backed provider on Deepgram's Aura-2 voices over the SDK's sync speak v1 WebSocket: `deepgram` extra, lazy import, `linear16` PCM with no conversion, native text stream (sender/reader/worker threads, 100 ms poll), pending-text backpressure (flushed segments, one unanswered `Flush`), aborted sockets closed off the worker | Implemented |
| [tone-module.md](modules/tone.md) | Fixture module: sine tone proportional to text length (a tone per piece for a text stream); no key, no extra, no files; the live tier's default module | Implemented |
| [audiofile-module.md](modules/audiofile.md) | Fixture module: text → WAV file list with a default file (a file per sentence for a text stream), 16-bit mono WAV validated at construction, paths relative to `base_dir` | Implemented |
| [audio-player.md](audio-player.md) | `AudioPlayer`, sounddevice integration, stream lifecycle, per-module sample rate | Implemented |
| [audio-sink.md](audio-sink.md) | `AudioSink` Protocol, constructor sink injection, `TTSEngine.sample_rate`, lazy sounddevice import, one drain per `say`/`say_stream` utterance | Implemented |

## Status legend

- **Not started** — no design decisions made yet
- **Draft** — actively being defined; contains load-bearing open questions
- **Stable** — design settled, reviewed and validated (open questions are deferrals only), ready to implement but not necessarily implemented yet. The design-review gate, before code.
- **Implemented** — a **Stable** spec that a `Done` plan has built: the code now exists and matches the spec
- **Updated** — an **Implemented** spec since edited in a way that needs new code, so the code no longer matches; a new plan is needed (or in progress). Returns to **Implemented** once that plan is `Done`.
