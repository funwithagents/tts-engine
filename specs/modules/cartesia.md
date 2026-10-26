---
code:
  - src/tts_engine/modules/cartesia/__init__.py
  - src/tts_engine/modules/cartesia/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_cartesia.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Cartesia Module

**Status:** Implemented

## Overview

`cartesia` implements `TTSModule` using Cartesia's hosted **Sonic** models (`sonic-3.6` by default) through the official `cartesia` Python SDK and its TTS WebSocket. It asks for raw `pcm_s16le` output, which is signed 16-bit mono PCM at the module's declared rate, so audio goes from the socket to `callback` with only a base64 decode: no codec and no float conversion. It is one API-backed provider among others, not the default and not part of the base install, and ships behind the `cartesia` packaging extra (see "Dependencies").

Its shape is the Deepgram one: a **synchronous** SDK socket driven from the cancellable worker, with no private event loop. It is a **native text-stream** backend. A Cartesia *context* takes repeated transcript pieces (`continue: true`) and starts speaking before the text is complete, so `synthesize_text_stream` forwards text as it arrives and does no sentence grouping. A complete text goes over the same socket as a single piece.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "cartesia"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Cartesia API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Cartesia's own convention is `CARTESIA_API_KEY`). Lets a config file be committed with no secret. |
| `voice_id` | non-empty string | yes | — | A Cartesia voice id (a UUID from the voice library or a cloned voice), e.g. `6ccbfb76-1fc6-48f7-b71d-91ac6298247b` ("Tessa", English). |
| `model` | non-empty string | no | `"sonic-3.6"` | Sonic model id: a dated snapshot (`sonic-3-2026-01-12`), a family alias (`sonic-3.6`, `sonic-3.5`, `sonic-3`) or `sonic-latest` (which moves with Cartesia's releases). |
| `sample_rate` | integer | no | `24000` | Output PCM rate. One of `8000`, `16000`, `22050`, `24000`, `44100`, `48000`, the raw-output rates the SDK types. |
| `language` | non-empty string | no | — (API default) | Language code of the transcript (`en`, `fr`, `de`, …). |
| `speed` | number | no | — (API default 1.0) | Speaking-rate multiplier, `0.6`–`1.5` (Sonic 3 `generation_config.speed`). |
| `volume` | number | no | — (API default 1.0) | Volume multiplier, `0.5`–`2.0` (`generation_config.volume`). |
| `emotion` | non-empty string | no | — (neutral) | An emotion name (`happy`, `calm`, `sympathetic`, …; `generation_config.emotion`). The set is the API's; the module only checks it is a string. |
| `max_buffer_delay_ms` | integer | no | — (API default 3000) | How long the server may hold text waiting for more context before it starts generating, `0`–`5000`. Lower trades prosody for latency on fragments with no sentence end. |

### API key resolution

Same rules as ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. A non-string value raises `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` naming the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `Cartesia(api_key=...)`.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `voice_id`: a non-empty string (required).
- `model`: a non-empty string (defaulted when absent).
- `sample_rate`: if present, an integer in `{8000, 16000, 22050, 24000, 44100, 48000}`. Booleans are rejected rather than treated as integers.
- `language`, `emotion`: if present, non-empty strings.
- `speed`, `volume`: if present, numbers (not bools) in `[0.6, 1.5]` and `[0.5, 2.0]`.
- `max_buffer_delay_ms`: if present, an integer (not a bool) in `[0, 5000]`.

Optional fields are **left out of the request when unset**, so the API's own defaults apply. `speed`, `volume` and `emotion` go in one `generation_config` object, sent only when at least one is set. Whether a model or voice exists is only known to the API: an unknown one fails at the first synthesis as `TTSError` (the server answers an `error` event, e.g. `Model not found` with status 404).

## Implementation notes

### SDK entry point

```python
from cartesia import Cartesia

client = Cartesia(api_key=key)
with client.tts.websocket_connect(  # TTSResourceConnection over websockets.sync
    websocket_connection_options={"max_queue": None},  # see "End of utterance"
) as ws:
    ctx = ws.context(
        context_id=str(uuid.uuid4()),
        model_id=self._model,
        voice=self._voice_id,
        output_format={"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate},
        # language=..., generation_config=..., max_buffer_delay_ms=... only when set
    )
    ctx.push("Hello ")        # continue=true
    ctx.no_more_inputs()      # empty transcript, continue=false
    raw = ws.recv_bytes(timeout=0.1)   # raises TimeoutError when nothing arrives
    event = ws.parse_event(raw)        # chunk | flush_done | done | timestamps | error
    ctx.cancel()
```

One client is built in `__init__` and reused. Each utterance opens **one socket** (`websocket_connect()`, a context manager whose exit closes the WebSocket) and one or more contexts on it (see "Context expiry"). The engine's `say` lock means at most one socket is open at a time.

The module reads the socket with `recv_bytes(timeout=...)` + `parse_event` rather than the SDK's `ctx.receive()` generator: `receive()` ends for good on its first timeout, and one utterance can span several contexts. Events are dispatched on their `type` string:

| `type` | Meaning | Module action |
|---|---|---|
| `chunk` | base64 PCM (`.audio` decodes it) | fed to `callback` |
| `done` | every input of the context has been spoken, or the context expired | ends the utterance when it is the final context (below) |
| `error` | `title`, `message`, `status_code`, `error_code` | `TTSError` for the current context; logged and ignored for a context already left behind |
| `flush_done`, `timestamps`, `phoneme_timestamps` | — | ignored (the module never flushes or asks for timestamps) |

### Output format and sample rate

`{"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate}` gives headerless signed 16-bit little-endian mono PCM at exactly `sample_rate` (confirmed live: 7392–9600-byte chunks at 24 kHz). That matches the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with no conversion in the module. As a cheap guard, the module carries an odd trailing byte over to the next chunk, so `callback` only ever gets whole samples. `sample_rate` returns the configured value and is fixed for the module's lifetime.

### Threads: sender and worker

The sync socket allows a send concurrent with a receive. Unlike Deepgram's, its `recv_bytes` takes a timeout, so no reader thread is needed:

- **Worker** (from `run_cancellable_worker` / `run_text_stream_worker`): opens the socket, starts the sender, then reads with a **100 ms** `recv_bytes` timeout, checking `stop` and the sender's error on every wake, and calls `callback` for audio. It is the only thread that calls `callback`.
- **Sender** (daemon): pulls `pieces`, pushes text, opens new contexts when needed, then ends the final context.

`synthesize_text(text)` runs this same machinery with `iter([text])` as the piece source.

### Text stream (native)

`synthesize_text_stream` is a **true stream-to-stream** pass. The sender groups the bridge's `pieces` at **word boundaries** with `split_at_last_space`, so a sub-word LLM token never reaches the server alone, and pushes each non-empty group to the current context. The server buffers on its own (`max_buffer_delay_ms`): live, a complete sentence started playing ~0.1 s after it was pushed, and a fragment with no sentence end waited the default 3 s (0.27 s with `max_buffer_delay_ms: 200`).

At end of stream the sender pushes the held-back remainder, then `no_more_inputs()` on the current context, which becomes the **final context**; the utterance ends at that context's `done`.

Two server rules shape this, both confirmed live:

- **An empty transcript is rejected.** A push of `""` with `continue: true` is a 400 error, and so is `no_more_inputs()` on a context that never received text (`No valid transcripts passed`). The sender never pushes an empty group, and a stream that produced no text sends nothing: the worker returns with no audio, no error.
- **Contexts expire.** See next section.

### Context expiry and rotation

A context **expires about 7 s after its last audio** when no further input arrives: the server sends `done` on its own, and text pushed afterwards is silently dropped (no error, no audio; measured 7.0 s after the last chunk, with pauses of 10 s and 30 s). An LLM that pauses mid-answer (a tool call) would therefore lose the rest of its text.

The sender **rotates** to a fresh context on the same socket (a new `context_id`, same settings) before pushing, when the utterance has been **idle** for `rotate_after` seconds — no push and no audio chunk since then — or when the worker has seen the current context's early `done`:

- `rotate_after = max(4.0, max_buffer_delay + 1.0)` seconds (4 s with the API's default 3 s delay). Text the server still held would have been released after at most `max_buffer_delay`, and its audio would have reset the idle clock; so an idle context has nothing left to say and can be left behind without `no_more_inputs()`. The threshold stays at least 1 s under the observed 7 s expiry.
- The worker ignores the left-behind context's later `done` (its expiry) and logs any `error` it gets at `DEBUG`.
- The same rule applies at end of stream: if the current context is idle past `rotate_after` and no remainder is left, it is not ended (it has spoken everything); the utterance ends once its audio is in, without a final `done`.

A second context on one socket was validated live: its audio plays after the first context's, with no overlap, because rotation only happens once the first is silent.

### End of utterance

The worker stops when the final context's `done` arrives, or, when there is no final context (empty stream, or the last context was idle), as soon as the sender has finished. It then sends `cancel` for every context it opened (ignoring failures), so the server stops any generation still running, and closes the socket **on a separate daemon thread**. Live, the server holds the close handshake for about 1 s in many cases, even after `done` and `cancel` (other times it answers in 0.04 s). Callbacks have already stopped, so the late close is harmless, and `say` returns about 0.18 s after its last chunk was fed instead of ~1.2 s.

The socket is opened with **no receive flow control** (`max_queue: None`). With websockets' default of 16 frames, a worker blocked in `callback` on real-time playback left the server's audio unread, websockets stopped reading the TCP socket, and the close frame could not get through: a cancel took the full 10 s close timeout. Without the limit, websockets keeps reading in its background thread. The unread audio of one utterance is at most a few MB (the server generates far faster than real time).

A connection error or server close before the final `done` raises `TTSError` (audio was lost).

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"). The worker sees `stop` within one 100 ms poll (sooner when audio is flowing), stops calling `callback` and leaves its loop. It then sets `stop` itself, which releases a sender waiting on text, sends `cancel` for every context so the server stops generating, and hands the socket to the closer thread (previous section). The coroutine re-raises `CancelledError` once the worker thread has exited. Measured live with a real-time callback: `say` and `say_stream` cancels surface in 0.05–0.19 s.

### Dependencies and lazy import

Requires the `cartesia` extra: `pip install tts-engine[cartesia]` / `uv sync --extra cartesia`, declared in `[project.optional-dependencies]` as `cartesia = ["cartesia[websockets]>=4.2,<5"]` and added to the `all` aggregate. The `websockets` extra of the SDK is what provides the socket client. The `<5` pin is there because the module relies on the 4.x `websocket_connect` / `context` API (hand-written in the SDK's `lib/_tts.py`, flagged upstream to be regenerated). The SDK caps `websockets<16`, which resolves the shared `websockets` to 15.x for the whole environment; the `deepgram` and `gemini` extras accept it.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `cartesia/module.py` must **not** import `cartesia` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    from cartesia import Cartesia
    import websockets.sync.client  # noqa: F401  (the SDK's [websockets] extra)
except ImportError as exc:
    raise ConfigError(
        "The 'cartesia' module requires the cartesia extra: pip install tts-engine[cartesia]"
    ) from exc
```

`tests/modules/test_lazy_imports.py` adds `cartesia` to the poisoned modules and the `cartesia` row to its construction loop.

### Error handling

- Any failure of the SDK or the socket is re-raised as `TTSError(f"Cartesia request failed: {exc}")`, chained via `raise ... from exc`: a rejected handshake (HTTP 401 bad key, raised by `websockets` as `InvalidStatus`), a connection closed before the final `done`, a failed send on the sender thread.
- An `error` event for the current context becomes `TTSError("Cartesia request failed: <title>: <message> (status <status_code>)")`.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged, because `callback` is called outside the provider `try`.
- A text-stream failure (the async source raising) aborts the utterance and propagates unchanged through `run_text_stream_worker`; no `no_more_inputs()` is sent, and every context is cancelled.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

## Module ID

Registered in `REGISTRY` as `"cartesia"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_cartesia.py`)**: `cartesia` (and `websockets.sync.client` when absent) is **faked** in `sys.modules`. The fake `Cartesia` client has a `tts.websocket_connect()` returning a context manager over a fake connection whose `context(**kw)` records its settings and returns a fake context (`push`, `no_more_inputs`, `cancel`, all recorded). The connection's `recv_bytes(timeout)` serves a queue of scripted events (chunks with base64 audio, `done`, `error`, exceptions) and raises `TimeoutError` when empty; by default it answers like the live API — one chunk per push, `done` after `no_more_inputs`. The tests cover: A float `sample_rate` (`24000.0`) is among the rejected values.
  - config: `ConfigError` on a missing extra, a missing or empty key, a missing `voice_id`, and each invalid field (parametrized); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the context settings (defaults, and `language` / `generation_config` / `max_buffer_delay_ms` only when set);
  - complete text: the connect options (`max_queue: None`); pushes then `no_more_inputs`; audio reaching the callback decoded, with `flush_done`/`timestamps` skipped; the odd-byte carry-over; `cancel` sent and the socket closed after `done`;
  - errors: connect failure, an `error` event, a connection close before `done`, and a sender failure wrapped in `TTSError`; callback errors propagating unchanged with the socket closed; cancellation mid-audio stopping callbacks before `CancelledError`, with every context cancelled;
  - text stream: word groups pushed, never an empty push; an empty stream sending nothing and returning; audio reaching the callback before the source has yielded its last piece; a source error propagating unchanged with no `no_more_inputs`; cancellation while the source is open and the server silent, surfacing promptly;
  - rotation (with the threshold constants patched small and `max_buffer_delay_ms: 0`): an idle pause opens a second context that receives the later text and the final `no_more_inputs`; an early `done` on the current context forces rotation; a late `done`/`error` from a left-behind context is ignored.
- **Live (`tests-e2e/test_modules.py`)**: one `MODULES` row (`cartesia`, `api_key_env: "CARTESIA_API_KEY"`, the "Tessa" voice id) plus `_REQUIRED_IMPORT["cartesia"] = "cartesia"` in `support.py`. Every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is missing ([testing.md](../testing.md), "Live tier"). CI maps an optional `CARTESIA_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `cartesia==4.2.0`

Confirmed from the package and from live calls on 2026-10-26 (API version `2026-08-14`, the SDK's default):

- `from cartesia import Cartesia`; `Cartesia(api_key=...)` sends it as a bearer token. `client.tts.websocket_connect()` is a context manager over `websockets.sync.client.connect` to `wss://api.cartesia.ai/tts/websocket`, yielding a `TTSResourceConnection` with `context(context_id=, model_id=, voice=, output_format=, language=, generation_config=, max_buffer_delay_ms=, ...)`, `recv_bytes(timeout=)` (raises `TimeoutError`), `parse_event`, `send`, `close`. A context has `push(text)`, `no_more_inputs()`, `cancel()`, `receive()`. `voice` is a plain id string.
- `sonic-3.6`, `sonic-3.5`, `sonic-3` and `sonic-latest` all answer; `sonic-latest` currently produces the same audio as `sonic-3.6`. First audio 0.18–0.22 s after a complete sentence on `sonic-3.6` / `sonic-3` (one `sonic-3.5` call took 1.7 s). An unknown model → `error` event `Model not found` / 404 / `model_not_found`. A bad key → HTTP 401 at the handshake (`InvalidStatus`).
- Empty transcript with `continue: true` → 400 `transcript must not be empty when continue is true unless flushing`. `no_more_inputs()` alone on a fresh context → 400 `No valid transcripts passed`.
- Pauses: a push 3.5 s after the last audio is still spoken; after 10 s or 30 s the context has already sent `done` (at ~7.0 s after its last chunk) and the push is dropped silently. A second context on the same socket works.
- Close: ~1.0–1.1 s after `done`; with `cancel` first, sometimes ~0.04 s and sometimes still ~1.0 s. Mid-generation with the default `max_queue` and a slow reader: the 10 s close timeout; with `max_queue: None` and `cancel`: ~1.1 s.
- Through the module: first audio 0.22–0.24 s for a text stream, 0.38–0.68 s for a 30-sentence `say`; a text stream paused 10 s mid-answer rotates and speaks the rest; `say` returns ~0.18 s after its last chunk.

## Open questions

Deferrals only (design is settled):

1. **Connection reuse.** One socket per utterance is the simplest correct shape, as for the other providers. Cartesia's sockets are designed to be long-lived (contexts multiplex on one connection), which would save the handshake; it needs care with the server's idle timeout. Measure before adding it.
2. **Timestamps.** `add_timestamps` gives word timings that could drive captions; the engine has no channel for them yet.
3. **`base_url` / `websocket_base_url`** for a regional or proxied endpoint. The client takes both; not needed for the hosted API.
4. **Expiry threshold.** The 7 s expiry is measured, not documented (the docs say "1 second after the last audio output"). If Cartesia changes it, `rotate_after` moves; no config field until there is a reason.
