# tts-engine

A streaming text-to-speech engine for Python. Choose a TTS module, construct a `TTSEngine`, and call `await engine.say(text)`, or `await engine.say_stream(text_stream)` to speak an LLM response while it is still being generated.

Audio is synthesized and consumed chunk by chunk, minimizing the delay before playback begins. Speech comes from a provider module you install as an extra: the ElevenLabs, Gradium, Mistral, Deepgram, Gemini, Cartesia or OpenAI cloud APIs (OpenAI's Realtime API through the `openai` module; its speech endpoint through the `openai-compatible` module, which also reaches any OpenAI-compatible server), or the local pocket-tts, Phonon, Qwen3-TTS, Kokoro or Piper models. There is no default provider. Two built-in fixture modules, `tone` and `audiofile`, stand in for a provider in tests and demos. By default, audio plays on the machine running the engine; applications can instead provide their own audio sink to capture or route the PCM stream.

Agent-friendly tools are included as an optional interface over the same engine, and an MCP server is available through the `mcp` extra.

**Start here:** the [quick start](#quick-start) below, then [docs/index.md](docs/index.md) for the documentation by task.

## Installation

This project requires Python 3.11+ and uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
git clone https://github.com/funwithagents/tts-engine.git
cd tts-engine
uv sync --no-dev
```

`--no-dev` is what keeps that install minimal: `uv sync` includes the `dev` group by default, and `dev` depends on `tts-engine[all]`, so a bare `uv sync` here builds the full development environment — every extra, `torch` included (see [How it is developed](#how-it-is-developed)).

Default local playback uses `sounddevice`, which requires PortAudio. On Ubuntu:

```bash
sudo apt-get install libportaudio2
```

The base installation contains the engine, tools, audio player, and the `tone`/`audiofile` fixture modules, but no speech provider and no server. Install the provider you want as an extra, and add the `mcp` extra to run the MCP server:

```bash
uv sync --no-dev --extra pocket            # one provider (every extra: docs/modules/index.md)
uv sync --no-dev --extra pocket --extra mcp   # a provider and the MCP server
uv sync --no-dev --extra hosted-modules    # every hosted provider
uv sync --no-dev --extra all-modules       # every provider, local and hosted
uv sync --no-dev --all-extras              # every extra

# For an installed package:
pip install "tts-engine[pocket]"
pip install "tts-engine[mcp,pocket]"
pip install "tts-engine[all-modules]"   # every provider
pip install "tts-engine[all]"           # every extra
```

Selecting a provider whose extra is not installed fails at engine construction with a `ConfigError` naming the `pip install` command.

## Quick start

Choose a provider first and install its extra (see [the modules](#modules)). This example uses the local pocket-tts provider, which needs no API key (`uv sync --no-dev --extra pocket`). Construct an engine configuration and speak:

```python
import asyncio

from tts_engine import TTSEngine, TTSEngineConfig


async def main() -> None:
    config = TTSEngineConfig(module={"type": "pocket", "voice": "alba"})

    engine = TTSEngine(config)
    await engine.say("Hello from the TTS engine")


asyncio.run(main())
```

No server or protocol is involved. The text is synthesized through the configured module and streamed to the local audio device. Any other module works the same way: only the `module` block changes.

> **No key or extra yet?** Swap the module for `{"type": "tone"}` to check the engine and your audio output end to end. It plays a short sine tone rather than speech. It is a fixture module for tests, not a TTS backend.

## How the engine works

The reusable core has three parts:

```text
text        → TTSEngine.say()        → TTSModule → AudioSink
text stream → TTSEngine.say_stream() ↗                 └─ AudioPlayer by default
```

- `TTSEngine` owns the selected module and audio sink and exposes `say(text)` for a complete text and `say_stream(text_stream)` for text arriving over time.
- A `TTSModule` connects the engine to one synthesis backend. Exactly one module is selected when the engine is constructed.
- An `AudioSink` consumes the streaming PCM chunks. The built-in `AudioPlayer` sends them to the local audio device.

The module declares its output sample rate, and the engine configures the default player to match it. Calls to `say()` and `say_stream()` on the same engine are serialized, and each call returns after the sink has been drained.

An engine holds its module's client or loaded model for its lifetime. Release it with `await engine.close()`, or use the engine as an async context manager; after that, `say()` and `say_stream()` raise `RuntimeError`. The sink is not touched: an injected sink is yours to close.

```python
async with TTSEngine(config) as engine:
    await engine.say("Hello from the TTS engine")
```

## Modules

A module is a swappable synthesis backend, selected by the `type` of the `module` block. `hosted` modules call a cloud API (a key and a network), `local` ones run a model in process (no network once its weights are downloaded), and the `fixture` ones stand in for a provider in tests and demos. There is no default.

| Module | `kind` | Synthesis | Credentials |
|---|---|---|---|
| [`pocket`](docs/modules/pocket.md) | `local` | In-process model | None |
| [`phonon`](docs/modules/phonon.md) | `local` | In-process model | None |
| [`qwen3`](docs/modules/qwen3.md) | `local` | In-process model (Apple silicon) | None |
| [`kokoro`](docs/modules/kokoro.md) | `local` | In-process model | None |
| [`piper`](docs/modules/piper.md) | `local` | In-process model | None |
| [`elevenlabs`](docs/modules/elevenlabs.md) | `hosted` | ElevenLabs cloud API | API key |
| [`gradium`](docs/modules/gradium.md) | `hosted` | Gradium cloud API | API key |
| [`mistral`](docs/modules/mistral.md) | `hosted` | Mistral cloud API (Voxtral TTS) | API key |
| [`deepgram`](docs/modules/deepgram.md) | `hosted` | Deepgram cloud API (Aura-2, Flux TTS) | API key |
| [`gemini`](docs/modules/gemini.md) | `hosted` | Google Gemini API (Gemini TTS) | API key |
| [`cartesia`](docs/modules/cartesia.md) | `hosted` | Cartesia cloud API (Sonic) | API key |
| [`openai`](docs/modules/openai.md) | `hosted` | OpenAI Realtime API (`gpt-realtime-2.1-mini`) | API key |
| [`openai-compatible`](docs/modules/openai-compatible.md) | `hosted` | OpenAI's `/v1/audio/speech` (`gpt-4o-mini-tts`, `tts-1`) or an OpenAI-compatible server | API key |
| [`tone`](docs/modules/tone.md) | `fixture` | Sine tone, no synthesis | None |
| [`audiofile`](docs/modules/audiofile.md) | `fixture` | Pre-recorded WAV files, no synthesis | None |

Each module's page has its install line, every config field, its voices and its troubleshooting. [docs/modules/index.md](docs/modules/index.md) compares them, and [choosing a backend and a voice](docs/choosing-a-backend-and-voice.md) narrows them down for an application.

## Documentation

Start at [docs/index.md](docs/index.md), the documentation by task. The pages used most:

| | |
|---|---|
| [docs/configuration.md](docs/configuration.md) | The engine block, the loaders, relative paths, the output device, the output gain |
| [docs/modules/index.md](docs/modules/index.md) | Every module, with one page each |
| [docs/streaming.md](docs/streaming.md) | Speaking an LLM response while it is generated |
| [docs/audio-sinks.md](docs/audio-sinks.md) | The default player, and capturing or routing the audio yourself |
| [docs/agents-and-mcp.md](docs/agents-and-mcp.md) | `TTSTools` for an agent, and the MCP server |
| [docs/choosing-a-backend-and-voice.md](docs/choosing-a-backend-and-voice.md) | Where each backend runs, how fast it answers, its voices, and how to audition them |
| [docs/troubleshooting.md](docs/troubleshooting.md) | No audio, a missing extra, a voice louder or quieter than the others, and each backend's own errors |

## Examples

[examples/](examples/) holds one complete, runnable config per module (`config.<type>.json`), plus a variant for ElevenLabs v4 and one for Deepgram Flux. They are in the MCP server's format (an `engine` block and a `server` block), so with the `mcp` extra installed, `uv run tts-engine-mcp --config examples/config.tone.json` starts a server as is; the `engine` block is what a library config takes. They hold no secrets: a hosted module reads its key from the environment variable named in `api_key_env`.

## Latency benchmark

[benchmarks/](benchmarks/README.md) measures how long each module and model makes you wait before the first sound, and whether the audio then keeps up, through the same `TTSEngine` API you use. Where to look:

- [the Mac result's summary](benchmarks/results/20261027-1453_mac-m5-pro/summary.md): the numbers, one row per model, local backends then hosted ones, with the device and network in its `description.md`;
- [RESULTS.md](benchmarks/RESULTS.md): what stood out, and the decisions it led to;
- [its README](benchmarks/README.md): what each metric means, and how to run it on your own machine and connection.

## Voice audition

[tools/voice-audition/](tools/voice-audition/README.md) helps pick a voice: it builds a list of candidates from the backends' catalogs, has each one say the same sentence while measuring its time to first audio on your machine and its level, and writes a page to listen to them one by one, mark each Yes, Maybe or No, and read the `gain_db` that levels it with the others. It runs from a checkout; [its README](tools/voice-audition/README.md) has the commands, and [choosing a backend and a voice](docs/choosing-a-backend-and-voice.md) shows where it fits.

## Level

Each backend, model and voice speaks at its own level, and the engine plays it as it is: an application that switches voices, or plays several one after another, hears a volume jump at every change. The engine block's optional `gain_db` sets one static gain per engine, and `tts-engine-level`, installed with the package, finds its value for a voice:

```bash
uv run tts-engine-level --config engine.json
```

It has the engine say three sentences, measures the level of the speech with the silences left out, and prints the `gain_db` that brings the voice to a target level (−22 dBFS by default, `--target` to change it). Copy that value into the engine block next to the voice's `module`, and use the same target for every voice you want to match. The same measure is available from Python as `tts_engine.level.calibrate`, and the voice audition shows it for every candidate. [Output gain](docs/configuration.md#output-gain) has the details: running it with nothing installed, its options, clipping.

## Feedback and contributions

Bug reports, questions, reports from a platform or a provider account the author does not test on, and requests for a backend are welcome at [the issue tracker](https://github.com/funwithagents/tts-engine/issues). The project is maintained by one person and is not taking external pull requests for now; [CONTRIBUTING.md](CONTRIBUTING.md) says why and how to help anyway.

## How it is developed

`tts-engine` is a spec-driven project: every feature is designed before it is coded, pinned by tests at two levels, and checked by CI on every change.

- **Specs first.** Each concept has one spec under [specs/](specs/), the normative design with its rationale and open questions, indexed by [specs/_index.md](specs/_index.md); each backend has its own under [specs/modules/](specs/modules/). An implementation plan under [plans/](plans/) turns a settled spec into code. Each spec names the code and tests it governs, and a test keeps that map, the statuses and the documentation's links honest.
- **Two test tiers.** `tests/` is fast and deterministic: no network, no credentials, no audio hardware, every provider SDK faked. `tests-e2e/` drives the real providers and the machine's audio output, and skips a provider whose key or extra is missing ([specs/testing.md](specs/testing.md)).
- **CI on every change.** The static gate, the fast tier on Linux (x86_64 and arm64), macOS and Windows, and the live tier on Linux ([specs/ci.md](specs/ci.md)).
- **Measured, not assumed.** Design choices that depend on speed (a module's default model, holding a socket open) come from the latency benchmark, and the module specs cite the result they rest on.
- **[AGENTS.md](AGENTS.md) is the operating manual** for anyone, or any coding agent, working in the repository: the project map, the commands, how to add a module, how the specs and statuses are kept in step with the code.

The same checks, locally:

```bash
uv sync --dev                # every extra, torch included, so nothing is skipped for a missing extra
uv run ruff check .
uv run ruff format --check src tests tests-e2e benchmarks tools
uv run pyright
uv run pytest                # the fast tier
uv run pytest tests-e2e      # the live tier: real providers (keys from the environment) and the audio device
```

## License

MIT, see [LICENSE](LICENSE). Some extras pull in differently licensed dependencies or weights (GPL for `piper` and part of `kokoro`, CC BY-NC for the multilingual `phonon` models, per-voice dataset licenses for `piper`): their module pages say so.
