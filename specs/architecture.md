---
code:
  - src/tts_engine/engine.py
  - src/tts_engine/__init__.py
tests:
  - tests/test_engine.py
---

# Architecture

**Status:** Implemented

## Layers

The repo is three layers, from reusable core outward to transport:

1. **`TTSEngine`** (`engine.py`) — the reusable core. Built from a `TTSEngineConfig`, it holds a `TTSModule` + an `AudioSink` (the local `AudioPlayer` by default, or an injected custom sink) and exposes `say(text)` for a complete text and `say_stream(text_stream)` for text arriving over time (an LLM response). No protocol knowledge, no transport, no MCP.
2. **Tools** (`tools.py`) — `TTSTools`, an engine-bound class whose methods are provider- and transport-agnostic operations, e.g. `say(text) -> str`. They own input guards (empty text) and turn `TTSError` into a caller-friendly string. Three kinds of caller use them: the MCP wrappers (layer 3), a **non-MCP agent that registers a bound method directly** (`TTSTools(engine).say` is already `say(text) -> str`, keeping its name/docstring for the tool schema), and plain library code wanting the guarded contract. See [tools.md](tools.md), "Consumers".
3. **MCP** (`mcp.py`) — the MCP server. Registers thin `@mcp.tool()` wrappers that delegate to the tools layer and serves them over StreamableHTTP.

A library user stops at layer 1 or 2; an agent embeds layer 2 directly; an MCP client goes through layer 3.

## Component overview

```
┌─────────────────────────────────────────────────────┐
│ MCP Client (AI agent, Claude Desktop, test client)  │      Library caller
└──────────────────────┬──────────────────────────────┘      (own Python code)
                       │ StreamableHTTP (say tool call)               │
┌──────────────────────▼──────────────────────────────┐             │
│ MCP Server  (mcp.py)                                │             │
│  • Registers the say tool                           │             │
│  • Thin wrapper: delegates to tools.say             │             │
└──────────────────────┬──────────────────────────────┘             │
                       │                                            │
┌──────────────────────▼──────────────────────────────┐             │
│ Tools  (tools.py)                                   │             │
│  • TTSTools(engine).say(text) -> str                │◀────────────┘
│  • empty-text guard, TTSError → message             │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│ TTSEngine  (engine.py)                              │
│  • TTSEngine(TTSEngineConfig, *, sink=None)         │
│    → builds module (load_module) from config;       │
│      uses the injected sink or builds the default   │
│      AudioPlayer at the module's sample_rate        │
│  • say(text) → module.synthesize_text               │
│  • say_stream(text_stream)                          │
│      → module.synthesize_text_stream                │
│    both feed the sink via callback, then drain it   │
└────────────┬─────────────────────────┬──────────────┘
             │                         │
┌────────────▼──────────┐  ┌──────────▼──────────────┐
│ TTSModule             │  │ AudioSink  (audio.py)   │
│ (modules/base.py ABC) │  │  Protocol: feed / drain │
│  synthesize_text      │  │                         │
│  synthesize_text_     │  │ AudioPlayer (default)   │
│    stream             │  │  • Opens sounddevice    │
│                       │  │    output stream lazily │
│ elevenlabs/ gradium/  │  │  • feed() writes PCM    │
│  • Cloud APIs; text   │  │    to the device        │
│    stream sent over   │  │                         │
│    the socket as it   │  │ or an injected custom   │
│    arrives            │  │ sink (capture, network, │
│                       │  │ another pipeline)       │
│ pocket/               │  └─────────────────────────┘
│  • Runs pocket-tts    │
│    in-process; text   │
│    stream grouped     │
│    into sentences     │
│                       │
│ tone/ audiofile/      │
│  • fixture modules:   │
│    sine / WAV files   │
│                       │
│ All: callback per     │
│ PCM chunk, from one   │
│ worker thread         │
└────────────┬──────────┘
             │ HTTPS / WebSocket (cloud providers only)
┌────────────▼──────────┐
│ ElevenLabs / Gradium  │
└───────────────────────┘

Providers (`elevenlabs`, `pocket`) each ship behind a packaging extra; the fixture modules ship in the base install; no module is the default (see [tts-module-interface.md](tts-module-interface.md), "Module kinds").
The MCP layer ships behind the `mcp` extra; layers 1 and 2 need only the base install (see [project.md](project.md), "Dependency strategy for transports").
```

## `TTSEngine` construction

`TTSEngine` has a single constructor that takes the engine config, plus an optional audio sink:

```python
class TTSEngine:
    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        """Build the module (via load_module) from config. Use the injected sink,
        or build the default local AudioPlayer from config when sink is None."""
```

- The constructor is the entry point for all use (library and MCP). It reads `config.module` (the raw module dict, including `type`) through `load_module`. If no `sink` is given it builds the default `AudioPlayer` from `config.player` (a `PlayerConfig`) **and the module's declared `sample_rate`** — `AudioPlayer(device=config.player.device, sample_rate=module.sample_rate)`. The module owns the rate (see [tts-module-interface.md](tts-module-interface.md)); the player opens its output stream to match.
- **Sink injection** ([audio-sink.md](audio-sink.md)) makes the playback destination swappable: pass `sink=` to send synthesized PCM somewhere other than the local speaker (a robot pipeline, a buffer, a network stream). When a sink is injected the engine builds no `AudioPlayer` and never imports sounddevice, so the engine stays usable on hosts with no audio device. Backwards compatible — omitting `sink` reproduces today's local playback exactly.
- `TTSEngine.sample_rate` (read-only property) exposes the active module's declared rate, so a custom sink can resample the int16-mono PCM to its destination's format.
- Tests exercise `say()` behavior without touching a TTS provider or audio hardware by patching `load_module` and `AudioPlayer` in `engine.py` so the constructor yields fakes — dependency injection at the module-boundary rather than the constructor signature — or by injecting an in-memory fake sink directly.

See [configuration.md](configuration.md) for `TTSEngineConfig` / `PlayerConfig`.

## Data flow

### Through the MCP

1. MCP client sends a `say` tool call with `{"text": "Hello world"}`.
2. `mcp.py`'s `say` wrapper calls `tools.say(text)` on its `TTSTools(engine)`.
3. `TTSTools.say` guards empty text, then `await engine.say(text)`, mapping `TTSError` to an error string.
4. `engine.say` builds a `TTSOptions()` and calls `module.synthesize_text(text, options, callback=sink.feed)` — the sink being the injected one or the default `AudioPlayer`.
5. The module produces raw signed 16-bit PCM mono at its declared `sample_rate`, inside a single `asyncio.to_thread` worker: the ElevenLabs module opens an HTTPS streaming connection requesting MP3 (`mp3_44100_128`) and decodes each chunk via `miniaudio.stream_any` in-process; the pocket module runs local inference and converts each float32 chunk to int16; the `tone` module generates a sine wave and the `audiofile` module reads a WAV file.
6. As PCM chunks are produced, the module calls `sink.feed(chunk)` for each one.
7. With the default sink, `AudioPlayer.feed` writes the chunk to the open `sounddevice` output stream — playback begins on the first chunk. A custom sink does whatever its destination needs.
8. When the stream ends, `engine.say` returns; `TTSTools.say` returns `"OK"`; `mcp.py` returns it to the client.

### From library code

Steps 3–7 above, entered directly: application code calls `TTSTools(engine).say(text)` (for the guarded, string-returning contract) or `await engine.say(text)` (raw) — no MCP layer involved.

### From a text stream (library only)

The streaming input is an **engine-level** API: the host application owns the text source (an LLM SDK's response stream) and pipes it into the engine in-process. There is no tools-layer or MCP counterpart in v1 — a tool call carries one complete JSON argument, so it cannot stream text through a single call (see [tools.md](tools.md), "Open questions").

```python
async def llm_tokens() -> AsyncIterator[str]:
    async for event in llm_client.stream(prompt):
        yield event.text

await engine.say_stream(llm_tokens())
```

1. `engine.say_stream` takes the lock, builds a `TTSOptions()`, and calls `module.synthesize_text_stream(text_stream, options, callback=sink.feed)`. A plain (sync) iterable is wrapped into an async iterator first — a convenience for in-memory sequences and tests only; a blocking sync generator would stall the event loop, so a blocking source must be made async by the caller. A bare `str` is rejected with `TypeError` (it is an iterable of characters, almost certainly a mistake — `say` is the method for a complete text).
2. The module consumes the pieces as they arrive and feeds PCM as soon as it has any: a cloud provider forwards text to its socket and receives audio concurrently; a local model synthesizes each sentence as soon as it is complete (see [tts-module-interface.md](tts-module-interface.md), "`TTSModule` ABC"). Audio for the start of the response plays while the LLM is still producing the end.
3. When the stream is exhausted and the module has fed its last chunk, `say_stream` drains the sink and releases the lock. If the text stream raises, the utterance is aborted and the source's exception propagates unchanged; `TTSError` and callback errors propagate exactly as for `say`.

## Component responsibilities

| Component | Responsibility |
|-----------|---------------|
| `mcp.py` | MCP protocol, tool registration, StreamableHTTP; thin wrappers over `tools`; builds the `FastMCP` app (behind the `mcp` extra) |
| `tools.py` | `TTSTools`: engine-bound, provider/transport-agnostic operations (`say`); input guards; `TTSError` → string |
| `engine.py` | Builds the module from `TTSEngineConfig`; uses an injected `AudioSink` or builds the default `AudioPlayer` (at the module's `sample_rate`); `say()` and `say_stream()`; `sample_rate` property; no protocol knowledge |
| `modules/base.py` | Defines `TTSModule` ABC (`synthesize_text`, `synthesize_text_stream`), shared dataclasses (`TTSOptions`), and the shared helpers (`run_cancellable_worker`, `run_text_stream_worker`, `segment_sentences`, `resolve_path`) |
| `modules/elevenlabs/module.py` | ElevenLabs API interaction over HTTPS (complete text) and the stream-input WebSocket (text stream), MP3→PCM decoding via miniaudio (behind the `elevenlabs` extra, lazy import), config parsing |
| `modules/gradium/module.py` | Gradium realtime WebSocket (both input shapes; the text stream is forwarded as it arrives), raw PCM output (behind the `gradium` extra, lazy import), config parsing |
| `modules/pocket/module.py` | Local pocket-tts inference (behind the `pocket` extra, lazy import), sentence grouping for the text stream, float→int16 conversion, config parsing |
| `modules/tone/module.py` | Fixture module: sine tone proportional to text length, no dependencies beyond numpy |
| `modules/audiofile/module.py` | Fixture module: text → WAV file mapping with a default file, paths relative to `base_dir` |
| `audio.py` | Defines the `AudioSink` Protocol; `AudioPlayer` (its default impl): `sounddevice` output stream management, lazily imported; provider-agnostic consumer of the fixed PCM format contract |
| `config.py` | Parse and validate config; produce typed config dataclasses (`MCPServerConfig`, `TTSEngineConfig`, …), each with a `from_dict`/`from_json`/`from_json_file` constructor trio |
| `mcp_server_cli.py` | Argument parsing (`--config`, `--log-level`); imports the MCP stack inside `main`, exiting with the `pip install tts-engine[mcp]` hint when the extra is absent; `MCPServerConfig.from_json_file` → `TTSEngine(cfg.engine)` → MCP server; `logging.basicConfig(level=args.log_level)`; starts uvicorn |
| `__init__.py` | Public API surface: re-exports `TTSEngine`, `TTSEngineConfig`, `MCPServerConfig`, `TTSTools`, `AudioSink` |

## Public API

The package exposes the library entry points at the top level:

```python
from tts_engine import TTSEngine, TTSEngineConfig, MCPServerConfig, TTSTools, AudioSink
```

`TTSTools` is curated so agents can register its bound methods directly (see [tools.md](tools.md), "Consumers"). `AudioSink` is exported so embedders can type their own playback destination against the seam ([audio-sink.md](audio-sink.md)). Everything else (modules, the concrete `AudioPlayer`, mcp, mcp_server_cli) is reachable by submodule import but is not part of the curated top-level surface. None of the top-level names needs an extra: `MCPServerConfig` is a plain dataclass, while `tts_engine.mcp` itself requires the `mcp` extra.

## Threading / async model

- The MCP server runs under `uvicorn` (async).
- `engine.say` and `engine.say_stream` are `async` methods; each `await`s the module's synthesis coroutine.
- `AudioPlayer` uses a `sounddevice` output stream in blocking-write mode: `feed()` writes each PCM chunk directly to the stream. The module drives the callback from a single `asyncio.to_thread` worker, so `feed()` is only ever called from one thread at a time.
- `TTSEngine` owns an `asyncio.Lock` around the complete module-synthesis + sink-drain lifecycle. Concurrent library or MCP calls wait for exclusive access, so only one `say`/`say_stream` call is processed at a time and PCM streams cannot interleave — a sink never sees two overlapping utterances. For `say_stream` the lock is held for the whole text stream plus the audio tail: a slow LLM holds it as long as it talks.
- For `say_stream`, the text source is consumed on the engine's event loop (a pump task inside `run_text_stream_worker`) and handed to the module's worker thread through a queue; the engine loop is never blocked on the sink or on the backend.
- A module must stop invoking its callback before `synthesize_text()` / `synthesize_text_stream()` returns or raises, including cancellation. Every module requests cooperative worker shutdown and waits for its thread before propagating cancellation, so the engine can safely drain and release the lock.
- With the default sink, a long pause in the text stream (the LLM thinking) is an output **underrun**: the device plays silence until the next chunk, which is expected, not an error.
