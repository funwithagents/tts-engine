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

Its shape is the Deepgram one: a **synchronous** SDK socket driven from the cancellable worker, with no private event loop. It is a **native text-stream** backend. A Cartesia *context* takes repeated transcript pieces (`continue: true`) and starts speaking before the text is complete, so `synthesize_text_stream` forwards text as it arrives and does no sentence grouping. A complete text goes over the same socket in small slices (`slice_text`), so the same admission gate applies to both shapes.

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
| `timeout` | finite number > 0 (seconds) | no | `30` | Bound on the socket's handshake (passed to `websockets` as `open_timeout`, and polled by the module) and on a wait for the server: a context ended with no `done` and no message at all for this long is a stalled request. Either fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

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
- `timeout`: if present, a finite number (not a bool) `> 0`.

Optional fields are **left out of the request when unset**, so the API's own defaults apply. `speed`, `volume` and `emotion` go in one `generation_config` object, sent only when at least one is set. Whether a model or voice exists is only known to the API: an unknown one fails at the first synthesis as `TTSError` (the server answers an `error` event, e.g. `Model not found` with status 404).

## Implementation notes

### SDK entry point

```python
from cartesia import Cartesia

client = Cartesia(api_key=key)
with client.tts.websocket_connect(  # TTSResourceConnection over websockets.sync
    websocket_connection_options={"open_timeout": timeout},  # see "Cancellation"
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

One client is built in `__init__` and reused. Each utterance opens **one socket** (`websocket_connect()`, a context manager whose exit closes the WebSocket), entered through `open_cancellable` so the handshake honors `stop` and `timeout` (see "Cancellation"), and one or more contexts on it (see "Contexts"). The engine's `say` lock means at most one socket is open at a time, apart from the closing tail of a finished one.

The module reads the socket with `recv_bytes(timeout=...)` + `parse_event` on a reader thread rather than through the SDK's `ctx.receive()` generator: `receive()` ends for good on its first timeout, and one utterance can span several contexts. Events are dispatched on their `type` string:

| `type` | Meaning | Module action |
|---|---|---|
| `chunk` | base64 PCM (`.audio` decodes it) | fed to `callback` |
| `done` | every input of the context has been spoken, or the context expired | the context is acknowledged: the sender may open the next one; the utterance ends once the sender is done and every context it ended is acknowledged (below) |
| `error` | `title`, `message`, `status_code`, `error_code` | `TTSError` for the current or an ended, unacknowledged context; logged and ignored for one already acknowledged or left behind |
| `flush_done`, `timestamps`, `phoneme_timestamps` | — | ignored (the module never flushes or asks for timestamps) |

### Output format and sample rate

`{"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate}` gives headerless signed 16-bit little-endian mono PCM at exactly `sample_rate` (confirmed live: 7392–9600-byte chunks at 24 kHz). That matches the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with no conversion in the module. As a cheap guard, the module carries an odd trailing byte over to the next chunk, so `callback` only ever gets whole samples. `sample_rate` returns the configured value and is fixed for the module's lifetime.

### Threads: sender, reader, worker

The sync socket allows a send concurrent with a receive. Its `recv_bytes` takes a timeout, but the worker no longer reads it directly: a reader thread drains the socket continuously, so the close handshake can never be starved by unread audio and the audio read ahead can be counted (the Deepgram shape):

- **Worker** (from `run_cancellable_worker` / `run_text_stream_worker`): opens the socket through `open_cancellable`, starts the other two, then takes events from a `queue.Queue` with a **100 ms timeout**, checking `stop`, the sender's error, the idle rule and the stall rule on every wake, and calls `callback` for audio, subtracting what it feeds from the unread count. It is the only thread that calls `callback`.
- **Reader** (daemon): `recv_bytes(timeout=0.1)` + `parse_event` in a loop, each event (with its decoded audio) onto the queue, counting the audio bytes it has read (`unread`) and noting when the server last spoke and last sent audio. Ends when the socket fails or is closed.
- **Sender** (daemon): pulls `pieces`, pushes text, opens new contexts when needed, then ends the last context.

`synthesize_text(text)` runs this same machinery with `slice_text(text, 50)` as the piece source: the complete text goes in verbatim slices of at most 50 characters (cut at a sentence end, else at whitespace), a few seconds of speech each, so the admission gate has a grain and a gated context never looks idle (next sections).

### Text stream (native)

`synthesize_text_stream` is a **true stream-to-stream** pass. The sender groups the bridge's `pieces` at **word boundaries** with `split_at_last_space`, so a sub-word LLM token never reaches the server alone, and pushes each non-empty group to the current context. The server buffers on its own (`max_buffer_delay_ms`): live, a complete sentence started playing ~0.1 s after it was pushed, and a fragment with no sentence end waited the default 3 s (0.27 s with `max_buffer_delay_ms: 200`).

At end of stream the sender pushes the held-back remainder, then `no_more_inputs()` on the current context unless it is already ended or expired; the utterance ends once every ended context has been acknowledged with `done` (next section).

Two server rules shape this, both confirmed live:

- **An empty transcript is rejected.** A push of `""` with `continue: true` is a 400 error, and so is `no_more_inputs()` on a context that never received text (`No valid transcripts passed`). The sender never pushes an empty group, and a stream that produced no text sends nothing: the worker returns with no audio, no error.
- **Contexts expire.** See next section.

### Contexts: segments, idle rotation and the `done` acknowledgement

A context **expires about 7 s after its last audio** when no further input arrives: the server sends `done` on its own, and text pushed afterwards is silently dropped (no error, no audio; measured 7.0 s after the last chunk, with pauses of 10 s and 30 s). An LLM that pauses mid-answer (a tool call) would therefore lose the rest of its text. Contexts are also the only acknowledgement the module uses: `done` says every input of the context has been generated.

The module uses the context as its unit of flow control and of rotation, with one bookkeeping (`ended`: contexts given `no_more_inputs`; `done`: contexts the server acknowledged), all under one lock shared by the three threads:

- **One segment per context.** The sender ends the current context with `no_more_inputs()` at the first sentence end once it holds **600** characters, or at a word boundary at **900** at the latest (`cut_at_boundary`, the Deepgram constants). The next context opens, with a new `context_id` and the same settings, only once the ended one's `done` has arrived — so the server never holds more than one segment whose audio is not in, and audio keeps its order across contexts.
- **Idle rotation, acknowledged.** The worker ends a pushed context once the utterance has been **idle** for `rotate_after` seconds — no push and no audio chunk since then — with the same `no_more_inputs()`, so the server's `done` is in hand before the text resumes (an LLM pause) and the server's expiry can never swallow a push. `rotate_after = max(4.0, max_buffer_delay + 1.0)` seconds (4 s with the API's default 3 s delay): text the server still held has been released after at most `max_buffer_delay`, its audio would have reset the idle clock, and the threshold stays at least 1 s under the observed 7 s expiry. When the text resumes, the sender waits for that `done` (immediate for an idle context; after its remaining audio for a context whose first audio was late) before opening the next context, so a late-speaking context can never interleave with the next one.
- **Expiry anyway.** A `done` the server sends on its own (the context expired, or ended early) acknowledges the context too: the next push opens a new one without waiting. The worker logs an `error` for an acknowledged or left-behind context at `DEBUG` and ignores it.
- **At end of stream**, the sender ends the current context unless it is already ended or expired; the utterance ends once the sender is done and `ended ⊆ done`. An empty stream opens no context at all.

The push, the end-of-segment check and the worker's idle check run under the lock, so a push can never land on a context the worker is ending. Validated live on 2026-10-27: a text stream paused 10 s mid-answer rotated through two contexts, both ended and both acknowledged, and spoke the rest on the second; a 200-sentence `say` ran through one context per segment with the audio in order.

### End of utterance

The worker stops once the sender is done and every context it ended has its `done` (so for a complete text, at the last segment's `done`; for an empty stream, at once). It then sends `cancel` for every context it opened (ignoring failures), so the server stops any generation still running, and closes the socket **on a separate daemon thread**. Live, the server holds the close handshake for about 1 s in many cases, even after `done` and `cancel` (other times it answers in 0.04 s). Callbacks have already stopped, so the late close is harmless, and `say` returns about 0.18 s after its last chunk was fed instead of ~1.2 s. The reader keeps draining the socket until it is closed, which is what lets the server's close frame through: the socket runs with websockets' **default receive flow control** (the first version turned it off with `max_queue: None`, because a worker blocked in `callback` left the socket unread and the close frame stuck behind it for the 10 s close timeout; with a reader thread that cannot happen).

A connection error or server close while an ended context is still unacknowledged raises `TTSError` (audio was lost).

### Bounded unread audio

The server generates several times faster than real time while the default sink feeds at 1×, so the audio read and not yet fed would otherwise grow with the utterance's length (about 48 kB per second of audio ahead at 24 kHz). The sender therefore **admits** a push only while the unread audio is under **10 s** at the configured rate (`_MAX_UNREAD_SECONDS`): the reader counts bytes in, the worker counts bytes out, and the sender waits on the shared condition, polling `stop`, until playback has caught up. With one segment per context and the next context opened only after `done`, the unread audio never exceeds the lead plus one segment's audio — in the order of a minute for 900 characters, not of the utterance. A gated sender holds a slice for about the slice's own playback time (a few seconds, under `rotate_after`), so the gate does not make a context look idle; a sink slower than real time would, and the context would then rotate at the next push, which is harmless. The rule is the shared one in [tts-module-interface.md](../tts-module-interface.md), "Bounded unread audio"; measured values are under "Validated against".

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"). The worker sees `stop` within one 100 ms poll (sooner when audio is flowing), stops calling `callback` and leaves its loop. It then sets `stop` itself, which releases a sender waiting on text, on an acknowledgement or on the admission gate, sends `cancel` for every context so the server stops generating, and hands the socket to the closer thread (previous section). The coroutine re-raises `CancelledError` once the worker thread has exited. Measured live with a real-time callback: `say` and `say_stream` cancels surface in 0.05–0.2 s; a cancel mid-generation of a 30-sentence text surfaced in 0.20 s and the socket closed 1.05 s later, with the default receive queue (2026-10-27).

**During the handshake.** `websocket_connect` blocks in `websockets`' connect, so the worker enters it through `open_cancellable`: the connect runs on a helper thread while the worker polls `stop` every 100 ms against `timeout`, which is also passed to `websockets` as `open_timeout` (the SDK splats its connection options into `connect`, though its TypedDict does not list that key) so an abandoned handshake ends on its own. A cancel during the handshake surfaces within one poll with nothing sent; the connection, if it completes afterwards, is closed on the helper thread. A handshake slower than `timeout` is `TTSError("not open within …")`.

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

- Any failure of the SDK or the socket is re-raised as `TTSError(f"Cartesia request failed: {exc}")`, chained via `raise ... from exc`: a rejected handshake (HTTP 401 bad key, raised by `websockets` as `InvalidStatus`), a connection closed while an ended context is unacknowledged, a failed send on the sender thread or a failed `no_more_inputs` from the worker's idle check.
- An `error` event for the current context, or for an ended context not yet acknowledged, becomes `TTSError("Cartesia request failed: <title>: <message> (status <status_code>)")`.
- A **stall** — a context ended, not acknowledged, and no message of any kind from the server for `timeout` seconds — is `TTSError("Cartesia request failed: no answer within … s")`. A pause in the text stream is not a stall: once the worker has ended the idle context and its `done` is in, nothing is pending. A handshake slower than `timeout` is a `TTSError` too (see "Cancellation").
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged, because `callback` is called outside the provider `try`.
- A text-stream failure (the async source raising) aborts the utterance and propagates unchanged through `run_text_stream_worker`; the sender ends nothing more, and every context is cancelled.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` calls `Cartesia.close()`, releasing the SDK's HTTP client; every socket and context is per utterance.

## Module ID

Registered in `REGISTRY` as `"cartesia"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_cartesia.py`)**: `cartesia` (and `websockets.sync.client` when absent) is **faked** in `sys.modules`. The fake `Cartesia` client has a `tts.websocket_connect()` returning a context manager over a fake connection whose `context(**kw)` records its settings and returns a fake context (`push`, `no_more_inputs`, `cancel`, all recorded). The connection's `recv_bytes(timeout)` serves a queue of scripted events (chunks with base64 audio, `done`, `error`, exceptions) and raises `TimeoutError` when empty; by default it answers like the live API — one chunk per push, `done` after `no_more_inputs`. The tests cover: A float `sample_rate` (`24000.0`) is among the rejected values.
  - config: `ConfigError` on a missing extra, a missing or empty key, a missing `voice_id`, and each invalid field (parametrized, `timeout` included); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the context settings (defaults, and `language` / `generation_config` / `max_buffer_delay_ms` only when set);
  - complete text: the connect options (`open_timeout`); pushes then `no_more_inputs`; a long text sent in slices of at most 50 characters that concatenate back to it; audio reaching the callback decoded, with `flush_done`/`timestamps` skipped; the odd-byte carry-over; `cancel` sent and the socket closed after `done`;
  - errors: connect failure, an `error` event, a connection close before `done`, and a sender failure wrapped in `TTSError`; callback errors propagating unchanged with the socket closed; cancellation mid-audio stopping callbacks before `CancelledError`, with every context cancelled; an ended context left unacknowledged by a silent server as `TTSError` after `timeout`;
  - connection setup: a cancel during a held handshake surfacing within a second, the late socket closed with no context opened; a handshake slower than `timeout` as `TTSError`;
  - text stream: word groups pushed, never an empty push; an empty stream sending nothing and returning; audio reaching the callback before the source has yielded its last piece; a source error propagating unchanged with no `no_more_inputs`; cancellation while the source is open and the server silent, surfacing promptly;
  - contexts (with the thresholds patched small): a segment per context, each ended and the next opened only after the previous one's `done`, the text concatenating back; an idle pause ended by the worker and acknowledged, the later text going to a second context; a late first audio on the idle context arriving, in order, before the next context is opened; an early `done` on the current context forcing rotation, with a late `error` from it ignored; a stream ending on an idle context waiting for its `done`;
  - bounded unread audio: with the bound shrunk to one chunk and the callback blocked, the sender stops short of the whole text, then pushes the rest once playback resumes.
- **Live (`tests-e2e/test_modules.py`)**: one `MODULES` row (`cartesia`, `api_key_env: "CARTESIA_API_KEY"`, the "Tessa" voice id) plus `_REQUIRED_IMPORT["cartesia"] = "cartesia"` in `support.py`. Every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is missing ([testing.md](../testing.md), "Live tier"). CI maps an optional `CARTESIA_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `cartesia==4.2.0`

Confirmed from the package and from live calls on 2026-10-26 (API version `2026-08-14`, the SDK's default):

- `from cartesia import Cartesia`; `Cartesia(api_key=...)` sends it as a bearer token. `client.tts.websocket_connect()` is a context manager over `websockets.sync.client.connect` to `wss://api.cartesia.ai/tts/websocket`, yielding a `TTSResourceConnection` with `context(context_id=, model_id=, voice=, output_format=, language=, generation_config=, max_buffer_delay_ms=, ...)`, `recv_bytes(timeout=)` (raises `TimeoutError`), `parse_event`, `send`, `close`. A context has `push(text)`, `no_more_inputs()`, `cancel()`, `receive()`. `voice` is a plain id string.
- `sonic-3.6`, `sonic-3.5`, `sonic-3` and `sonic-latest` all answer; `sonic-latest` currently produces the same audio as `sonic-3.6`. First audio 0.18–0.22 s after a complete sentence on `sonic-3.6` / `sonic-3` (one `sonic-3.5` call took 1.7 s). An unknown model → `error` event `Model not found` / 404 / `model_not_found`. A bad key → HTTP 401 at the handshake (`InvalidStatus`).
- Empty transcript with `continue: true` → 400 `transcript must not be empty when continue is true unless flushing`. `no_more_inputs()` alone on a fresh context → 400 `No valid transcripts passed`.
- Pauses: a push 3.5 s after the last audio is still spoken; after 10 s or 30 s the context has already sent `done` (at ~7.0 s after its last chunk) and the push is dropped silently. A second context on the same socket works.
- Close: ~1.0–1.1 s after `done`; with `cancel` first, sometimes ~0.04 s and sometimes still ~1.0 s. Mid-generation with the default `max_queue` and a slow reader: the 10 s close timeout; with `max_queue: None` and `cancel`: ~1.1 s.
- Through the module: first audio 0.22–0.24 s for a text stream, 0.38–0.68 s for a 30-sentence `say`; a text stream paused 10 s mid-answer rotates and speaks the rest; `say` returns ~0.18 s after its last chunk.
- Bounded unread audio (2026-10-27, the module through `TTSEngine` with a sink feeding in real time, the unread count sampled 20× a second): a 200-sentence `say` peaked at 38.4 s of audio over a 75 s window, a 60-sentence `say` at 36.8 s and the same text as a word-by-word stream at 38.1 s over 40 s — the lead plus one segment, a plateau, where the server (several times faster than real time) would otherwise have run minutes ahead. First audio 0.24–0.29 s, unchanged by the gate. The same day: a 10 s pause mid-stream gave two contexts, both ended and acknowledged, the rest spoken on the second; a cancel after three chunks of a 30-sentence `say` surfaced in 0.20 s, socket closed 1.05 s later, with the default receive queue; no module thread left alive afterwards.

## Open questions

Deferrals only (design is settled):

1. **Connection reuse.** One socket per utterance is the simplest correct shape, as for the other providers. Cartesia's sockets are designed to be long-lived (contexts multiplex on one connection), which would save the handshake; it needs care with the server's idle timeout. Measure before adding it.
2. **Timestamps.** `add_timestamps` gives word timings that could drive captions; the engine has no channel for them yet.
3. **`base_url` / `websocket_base_url`** for a regional or proxied endpoint. The client takes both; not needed for the hosted API.
4. **Expiry threshold.** The 7 s expiry is measured, not documented (the docs say "1 second after the last audio output"). If Cartesia changes it, `rotate_after` moves; no config field until there is a reason.
5. **Context flushing instead of rotation.** The API's `flush` (an empty push with `flush: true`, answered by `flush_done`) would acknowledge a segment without ending its context, keeping prosody continuous across segments; its rate limit is unknown. Contexts were chosen because two of them on one socket were already validated live. Measure before switching.
