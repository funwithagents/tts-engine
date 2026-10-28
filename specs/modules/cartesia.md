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

Every utterance's contexts go over **one socket the module opens at construction and holds for its lifetime** (see "One socket, held for the module's lifetime"), replaced only when it has died or sat idle near the server's 5-minute idle limit: the WebSocket handshake (about 120 ms, half of a fresh socket's time to first audio) is paid once, not once per `say()`.

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
| `timeout` | finite number > 0 (seconds) | no | `30` | Bound on the socket's handshake, at construction and on every reconnect (passed to `websockets` as `open_timeout`, and polled by the module), and on a wait for the server: a context ended with no `done` and no message at all for this long is a stalled request. Either fails as `TTSError` after at most this long, which is also how long a cancel issued during a reconnect's stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

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

Optional fields are **left out of the request when unset**, so the API's own defaults apply. `speed`, `volume` and `emotion` go in one `generation_config` object, sent only when at least one is set.

The key is checked by the server at the handshake, which construction now runs (see "One socket, held for the module's lifetime"): a rejected key (HTTP 401 or 403, raised by `websockets` as `InvalidStatus`) is a **`ConfigError` at construction** naming the status, the point where the `openai` module rejects its key too; the same rejection on a later reconnect (a key revoked mid-life) is a `TTSError` of that utterance. Any other handshake failure at construction (no network, a timeout) is a `TTSError`. Whether a model or voice exists is only known once a context is used: an unknown one fails at the first synthesis as `TTSError` (the server answers an `error` event, e.g. `Model not found` with status 404), and the socket stays usable.

## Implementation notes

### SDK entry point

```python
from cartesia import Cartesia

client = Cartesia(api_key=key)
manager = client.tts.websocket_connect(  # TTSResourceConnectionManager over websockets.sync
    websocket_connection_options={"open_timeout": timeout},  # see "Cancellation"
)
ws = manager.__enter__()              # the handshake; manager.__exit__ closes the socket
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

One client is built in `__init__` and reused. Each socket is one `websocket_connect()` manager, entered through `open_cancellable` so the handshake honors `stop` and `timeout` (see "Cancellation"); a manager caches its connection and cannot be re-entered after its socket closed, so every reconnect builds a new one. Each utterance opens one or more contexts on the module's socket (see "Contexts").

The module reads the socket with `recv_bytes(timeout=...)` + `parse_event` on a reader thread rather than through the SDK's `ctx.receive()` generator: `receive()` ends for good on its first timeout, and one utterance can span several contexts. Events are dispatched on their `type` string:

| `type` | Meaning | Module action |
|---|---|---|
| `chunk` | base64 PCM (`.audio` decodes it) | fed to `callback` |
| `done` | every input of the context has been spoken, the context expired, or it was cancelled | the context is acknowledged: the sender may open the next one; the utterance ends once the sender is done and every context it ended is acknowledged (below) |
| `error` | `title`, `message`, `status_code`, `error_code` | `TTSError` for the current or an ended, unacknowledged context; logged and ignored for one already acknowledged or left behind |
| `flush_done`, `timestamps`, `phoneme_timestamps` | — | ignored (the module never flushes or asks for timestamps) |

Every server message names its `context_id` (`error` events included, confirmed live). An event whose context the current utterance did not open — the tail of an earlier, cancelled context, or anything arriving between utterances — is **foreign**: the reader drops it at DEBUG before it reaches the utterance (see "Routing by context").

### Output format and sample rate

`{"container": "raw", "encoding": "pcm_s16le", "sample_rate": rate}` gives headerless signed 16-bit little-endian mono PCM at exactly `sample_rate` (confirmed live: 7392–9600-byte chunks at 24 kHz). That matches the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with no conversion in the module. The worker feeds the chunks through the shared `PCMFeeder` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers"): an odd trailing byte is carried over to the next chunk, so `callback` only ever gets whole samples, and a chunk is handed over in slices of at most 100 ms, `stop` polled between them. `sample_rate` returns the configured value and is fixed for the module's lifetime.

### One socket, held for the module's lifetime

The module opens **one** socket in `__init__` and keeps it until `close()`; every utterance's contexts run over it, one utterance after another (the engine's say lock serializes them). Construction is already the expensive step of the engine's contract, so the handshake belongs there, as in the `openai` module ([openai.md](openai.md), "One socket, held for the module's lifetime"): an utterance soon after startup (an agent's first answer, an MCP server's first request) does not pay it — and that first handshake is the slow one (377 ms cold against 120 ms warm, measured 2026-10-28). Connecting lazily on the first `say` would save nothing for a module that then sits idle past the server's limit, and would move a bad key's rejection from construction to the first synthesis.

The socket object holds the SDK manager and connection, the reader thread, a `dead` flag (set when the reader ends or a send fails, with the exception that caused it), `last_message` (the monotonic time of the last message sent or received on it, the server's idle clock) and the **session** of the utterance using it, if any (below).

**Setup** — at construction and on every reconnect, the same sequence, bounded by `timeout`: the handshake (`websocket_connect(...).__enter__` through `open_cancellable`; construction passes a never-set `stop`), then the reader thread started on the new connection. There is no session message to wait for: Cartesia sends nothing until a context speaks. A rejected key is `ConfigError` at construction and `TTSError` on a reconnect (see "Validation"); any other failure is a `TTSError` at both points, and the module holds no socket after it.

**Per utterance** — the worker takes the socket once, before it starts the sender, after three checks run in this order:

1. **Dead socket → reconnect.** The socket is dead once its reader has ended — the server's idle close, a transport error — or a send on it failed. The worker closes the old one off the worker and runs the setup sequence; the utterance pays one handshake. A failure to reconnect fails this utterance as `TTSError`; the next utterance tries again. Nothing retries within an utterance: a socket that dies while an utterance has a context in flight fails that utterance as `TTSError`.
2. **Idle socket → replacement.** Cartesia closes a TTS socket after **300 s** with no message in either direction (measured to the second: close code 1000, reason `connection idle timeout`; the docs say "We close idle TTS WebSocket connections after 5 minutes"). WebSocket pings do **not** count as activity (a socket answering `websockets`' default 20 s pings was closed at 300.0 s like one with pings off), and the server sends no pings of its own. A socket whose `last_message` is older than **240 s** (`_IDLE_REPLACE`, 60 s under the limit) is closed off the worker and replaced before the utterance's first context. The reader would see the server's close anyway; replacing early avoids racing it — a push sent in the instant the server closes would be lost and fail the utterance — and leaves the utterance a minute before the limit to send its first text (an LLM's first token).
3. **The host slept → replacement.** The idle time is measured on both clocks, and the larger is taken: `last_message` is stamped with `time.monotonic()` and `time.time()` together. `time.monotonic()` stops while the host sleeps (`mach_absolute_time()` on macOS; `CLOCK_MONOTONIC` excludes suspend on Linux), so the monotonic figure alone cannot see a sleep; the wall clock keeps running. A wall-clock idle time exceeding the monotonic one by more than **20 s** (`_SLEPT`, one ping interval) means the host slept long enough to miss a keepalive ping: the socket may be half-open (the server closed it, or the network changed, with nothing read meanwhile) and is replaced the same way. A backward wall-clock step (NTP) only lowers the wall figure and is outweighed by the monotonic one. This is Deepgram's rule ([deepgram.md](deepgram.md), "One socket, held for the module's lifetime").

The module sends **no keepalive** traffic, although one would work (any application message resets the server's idle clock: a `cancel` of an unknown context every 120 s, answered by a 400 `error`, kept a socket open for 540 s). Cartesia recommends closing idle connections, counts open sockets against the account's limit (10× its concurrency), and a reconnect after four idle minutes costs one 120 ms handshake. No maximum age of an *active* socket was observed (one speaking every 90 s stayed open for 21 minutes; see "Validated against"); should one exist, a server close between utterances is handled by check 1 at no cost, one inside an utterance fails that utterance.

For a text stream the checks run when the utterance starts, before the stream has yielded anything, so a reconnect overlaps the wait for the source's first piece. A complete text that is blank returns before the checks and touches the socket not at all; an empty stream runs them (whether a stream is empty is only known at its end) and sends nothing.

Within an utterance the server's idle clock is reset by every push and chunk. A text stream that pauses for more than 300 s mid-utterance (its last context ended by the idle rotation, then nothing sent) loses the socket under it as before, and fails at its next push; see open question 1.

### Routing by context

One reader thread per **socket**, one session per **utterance**. The session (the shared state below: contexts opened, `ended`, `done`, `unread`, inbox) is attached to the socket by the worker when the utterance starts and detached when it ends, both under the socket's lock. For each event the reader:

- updates the socket's `last_message`;
- looks up the attached session and whether its `context_id` is one the session opened (the sender registers a context under the session's lock before its first push, so no event of it can arrive earlier);
- if so, counts its audio in the session's `unread`, notes `last_activity` and `last_message` on the session, and puts it in the session's inbox — exactly what the per-utterance reader did;
- otherwise drops it at DEBUG: never counted in `unread`, never resetting the utterance's idle clock, never seen by the worker.

When the reader ends (the socket closed or failed) it marks the socket dead and, if a session is attached, puts the exception in its inbox, so an utterance in flight fails as before; between utterances nothing is waiting and the next utterance reconnects. A detached socket's reader keeps draining it, so the socket's receive flow control never stalls and a server close is seen as it happens.

### Threads: sender, reader, worker

The sync socket allows a send concurrent with a receive. Its `recv_bytes` takes a timeout, but the worker never reads it directly: the socket's reader drains it continuously, so the close handshake can never be starved by unread audio and the audio read ahead can be counted (the Deepgram shape):

- **Worker** (from `run_cancellable_worker` / `run_text_stream_worker`, one per utterance): runs the dead and idle checks (a reconnect through `open_cancellable`), attaches a new session, starts the sender, then takes events from the session's inbox with a **100 ms timeout**, checking `stop`, the sender's error, the idle rule and the stall rule on every wake, and calls `callback` for audio, subtracting what it feeds from the unread count. It is the only thread that calls `callback`.
- **Reader** (daemon, one per socket, started by the setup sequence and living as long as the socket): `recv_bytes(timeout=0.1)` + `parse_event` in a loop, routing each event as above. Ends when the socket fails or is closed.
- **Sender** (daemon, one per utterance): pulls `pieces`, pushes text, opens new contexts when needed, then ends the last context.

`synthesize_text(text)` runs this same machinery with `slice_text(text, 50)` as the piece source: the complete text goes in verbatim slices of at most 50 characters (cut at a sentence end, else at whitespace), a few seconds of speech each, so the admission gate has a grain and a gated context never looks idle (next sections).

A sender of a finished utterance can outlive it (blocked on a text source the bridge is releasing), on a socket that later utterances use. It can never push again: every push and context open happens under the session's lock after a check of `stop`, and the worker sets `stop` before it takes that lock to collect the contexts to cancel, so a push either landed before (and its context is cancelled) or never happens.

### Text stream (native)

`synthesize_text_stream` is a **true stream-to-stream** pass. The sender groups the bridge's `pieces` at **word boundaries** with `split_at_last_space`, so a sub-word LLM token never reaches the server alone, and pushes each non-empty group to the current context. The server buffers on its own (`max_buffer_delay_ms`): live, a complete sentence started playing ~0.1 s after it was pushed, and a fragment with no sentence end waited the default 3 s (0.27 s with `max_buffer_delay_ms: 200`).

At end of stream the sender pushes the held-back remainder, then `no_more_inputs()` on the current context unless it is already ended or expired; the utterance ends once every ended context has been acknowledged with `done` (next section).

Two server rules shape this, both confirmed live:

- **An empty transcript is rejected.** A push of `""` with `continue: true` is a 400 error, and so is `no_more_inputs()` on a context that never received text (`No valid transcripts passed`). The sender never pushes an empty group, and a stream that produced no text sends nothing: the worker returns with no audio, no error.
- **Contexts expire.** See next section.

### Contexts: segments, idle rotation and the `done` acknowledgement

A context **expires about 7 s after its last audio** when no further input arrives: the server sends `done` on its own, and text pushed afterwards is silently dropped (no error, no audio; measured 7.0 s after the last chunk, with pauses of 10 s and 30 s). An LLM that pauses mid-answer (a tool call) would therefore lose the rest of its text. Contexts are also the only acknowledgement the module uses: `done` says every input of the context has been generated.

The module uses the context as its unit of flow control and of rotation, with one bookkeeping per utterance (`ended`: contexts given `no_more_inputs`; `done`: contexts the server acknowledged), all under one lock shared by the three threads. A context never spans utterances: each utterance opens its own, with fresh `context_id`s, and leaves none open behind it (ended and acknowledged, or cancelled).

- **One segment per context.** The sender ends the current context with `no_more_inputs()` at the first sentence end once it holds **600** characters, or at a word boundary at **900** at the latest (`cut_at_boundary`, the Deepgram constants). The next context opens, with a new `context_id` and the same settings, only once the ended one's `done` has arrived — so the server never holds more than one segment whose audio is not in, and audio keeps its order across contexts.
- **Idle rotation, acknowledged.** The worker ends a pushed context once the utterance has been **idle** for `rotate_after` seconds — no push and no audio chunk since then — with the same `no_more_inputs()`, so the server's `done` is in hand before the text resumes (an LLM pause) and the server's expiry can never swallow a push. `rotate_after = max(4.0, max_buffer_delay + 1.0)` seconds (4 s with the API's default 3 s delay): text the server still held has been released after at most `max_buffer_delay`, its audio would have reset the idle clock, and the threshold stays at least 1 s under the observed 7 s expiry. When the text resumes, the sender waits for that `done` (immediate for an idle context; after its remaining audio for a context whose first audio was late) before opening the next context, so a late-speaking context can never interleave with the next one.
- **Expiry anyway.** A `done` the server sends on its own (the context expired, or ended early) acknowledges the context too: the next push opens a new one without waiting. The worker logs an `error` for an acknowledged or left-behind context at `DEBUG` and ignores it.
- **At end of stream**, the sender ends the current context unless it is already ended or expired; the utterance ends once the sender is done and `ended ⊆ done`. An empty stream opens no context at all.

The push, the end-of-segment check and the worker's idle check run under the lock, so a push can never land on a context the worker is ending. Validated live on 2026-10-27: a text stream paused 10 s mid-answer rotated through two contexts, both ended and both acknowledged, and spoke the rest on the second; a 200-sentence `say` ran through one context per segment with the audio in order.

### End of utterance

The worker stops once the sender is done and every context it ended has its `done` (so for a complete text, at the last segment's `done`; for an empty stream, at once). Every context the utterance opened is then acknowledged, so there is nothing to cancel and nothing to close: the worker detaches its session and returns, and the socket waits for the next utterance. `say` returns as soon as the last chunk has been fed.

On any other way out — `stop`, an `error` event, a stall, a sender or source failure, a `callback` exception — the worker sets `stop`, then sends `cancel` for every context it opened that has **no `done` yet** (best effort; failures logged at DEBUG), detaches its session and returns **without closing the socket**. Contexts already acknowledged are not cancelled: the server answers a cancel of a finished context with a 400 `error` (`The requested context ID does not exist or may have already been cancelled.`), harmless once foreign but noise. Two exceptions drop the socket instead of keeping it (closed off the worker; the next utterance reconnects): a **stall**, after which the socket's state is unknown, and a **transport failure**, after which it is dead anyway.

The per-utterance socket used to be closed on a separate daemon thread at this point, because the server held the close handshake for about 1 s in many cases. That close is now off the hot path entirely: it happens only when a socket is replaced or the module closes, and always off the calling thread. The socket runs with websockets' **default receive flow control**; its reader drains it at all times, so a worker blocked in `callback` cannot leave the close frame stuck behind unread audio (the reason the first version turned it off with `max_queue: None`).

A connection error or server close while an ended context is still unacknowledged raises `TTSError` (audio was lost).

### Bounded unread audio

The server generates several times faster than real time while the default sink feeds at 1×, so the audio read and not yet fed would otherwise grow with the utterance's length (about 48 kB per second of audio ahead at 24 kHz). The sender therefore **admits** a push only while the unread audio is under **10 s** at the configured rate (`_MAX_UNREAD_SECONDS`): the reader counts bytes in, the worker counts bytes out, and the sender waits on the shared condition, polling `stop`, until playback has caught up. With one segment per context and the next context opened only after `done`, the unread audio never exceeds the lead plus one segment's audio — in the order of a minute for 900 characters, not of the utterance. A gated sender holds a slice for about the slice's own playback time (a few seconds, under `rotate_after`), so the gate does not make a context look idle; a sink slower than real time would, and the context would then rotate at the next push, which is harmless. The rule is the shared one in [tts-module-interface.md](../tts-module-interface.md), "Bounded unread audio"; measured values are under "Validated against".

The count belongs to the utterance's session, not to the socket: each utterance starts at zero, and the tail of an earlier utterance's cancelled context (one to three chunks, measured) is foreign, so it is neither counted nor fed.

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"). The worker sees `stop` within one 100 ms poll (sooner when audio is flowing), stops calling `callback` and leaves its loop. It then sets `stop` itself, which releases a sender waiting on text, on an acknowledgement or on the admission gate, and sends `cancel` for every unacknowledged context so the server stops generating (previous section). The socket is kept. The coroutine re-raises `CancelledError` once the worker thread has exited.

Measured live on 2026-10-28 on a held socket, cancelling a 20-sentence context after its first three to five chunks: the server sent one to three more chunks of it within 45–102 ms, then a `done` for the cancelled context; the next utterance, started on the same socket right after the cancel, had its first audio in 92–117 ms, the same as an uncancelled one. In one run of three the cancelled context's last chunk and `done` arrived after the next utterance's first chunk, which is why routing filters by `context_id` and not by time. A cancel sent right after the push (before any audio) still got one chunk and a `done`, 208 ms later. A cancel of a context that was pushed but never ended got its `done` the same way (74 ms). Earlier, with a socket per utterance: `say` and `say_stream` cancels surfaced in 0.05–0.2 s and the closing tail took about 1 s more off the worker (2026-10-27).

**During a reconnect's handshake.** `websocket_connect` blocks in `websockets`' connect, so the worker enters it through `open_cancellable`: the connect runs on a helper thread while the worker polls `stop` every 100 ms against `timeout`, which is also passed to `websockets` as `open_timeout` (the SDK splats its connection options into `connect`, though its TypedDict does not list that key) so an abandoned handshake ends on its own. A cancel during the handshake surfaces within one poll with nothing sent and the module holding no socket; the connection, if it completes afterwards, is closed on the helper thread, and the next utterance connects again. A handshake slower than `timeout` is `TTSError("not open within …")`. Construction uses the same helper with a never-set `stop`, so only `timeout` bounds it.

### Dependencies and lazy import

Requires the `cartesia` extra: `pip install tts-engine[cartesia]` / `uv sync --extra cartesia`, declared in `[project.optional-dependencies]` as `cartesia = ["cartesia[websockets]>=4.2,<5"]` and added to the `all` aggregate. The `websockets` extra of the SDK is what provides the socket client. The `<5` pin is there because the module relies on the 4.x `websocket_connect` / `context` API (hand-written in the SDK's `lib/_tts.py`, flagged upstream to be regenerated). The SDK caps `websockets<16`, which resolves the shared `websockets` to 15.x for the whole environment; the `deepgram` and `gemini` extras accept it.

The pin also covers one SDK behaviour the held socket depends on: `TTSResourceConnection.send` calls `_ensure_connected`, which on a closed connection "reconnects" by re-entering its manager — and the manager returns its cached, closed connection, so the send raises `ConnectionClosed` (confirmed live: `ConnectionClosedOK` after a close). The module never relies on that path: it checks `dead` before handing a socket to an utterance and builds a new manager for every reconnect. If a future SDK made `_ensure_connected` really reconnect, a send racing a server close would land on a socket no reader drains; a version bump must re-check this.

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

- Any failure of the SDK or the socket is re-raised as `TTSError(f"Cartesia request failed: {exc}")`, chained via `raise ... from exc`: a failed handshake on a reconnect (a rejected key included), a connection closed while an ended context is unacknowledged, a failed send on the sender thread or a failed `no_more_inputs` from the worker's idle check. A transport failure marks the socket dead; the next utterance reconnects.
- At construction a rejected key (HTTP 401/403, `InvalidStatus`) is `ConfigError` naming the status; any other handshake failure is `TTSError` (see "Validation"). A failed construction closes the client and holds no socket.
- An `error` event for the current context, or for an ended context not yet acknowledged, becomes `TTSError("Cartesia request failed: <title>: <message> (status <status_code>)")`. The socket is kept: an `error` leaves it usable (confirmed live after a 404 unknown model and a 400 empty transcript, the next utterance speaking normally on it). An `error` of a foreign context never reaches the utterance.
- A **stall** — a context ended, not acknowledged, and no message of any kind from the server for `timeout` seconds — is `TTSError("Cartesia request failed: no answer within … s")`, and the socket is dropped. A pause in the text stream is not a stall: once the worker has ended the idle context and its `done` is in, nothing is pending. A handshake slower than `timeout` is a `TTSError` too (see "Cancellation").
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged, because `callback` is called outside the provider `try`; the socket is kept.
- A text-stream failure (the async source raising) aborts the utterance and propagates unchanged through `run_text_stream_worker`; the sender ends nothing more, and every unacknowledged context is cancelled.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.
- A close failure (of a replaced socket, or at `close()`) is logged at DEBUG and never replaces an utterance's outcome.

### Lifetime

The `Cartesia` client and the socket are both built in `__init__` and both released by `TTSModule.close()` ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): the socket is closed **off the calling thread** (its close handshake took 38 ms to 1.03 s, measured 2026-10-28), then the client with `Cartesia.close()`, releasing its HTTP pool. `close()` is called with no utterance in flight (the engine takes its say lock first); a reader or closer thread still finishing a socket's tail afterwards holds nothing of the module's. A module whose socket is dead or missing at `close()` has only the client to close. A replaced socket is closed the same way, off the worker.

## Module ID

Registered in `REGISTRY` as `"cartesia"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_cartesia.py`)**: `cartesia` (and `websockets.sync.client` when absent) is **faked** in `sys.modules`. The fake `Cartesia` client has a `tts.websocket_connect()` returning a manager over a fake connection whose `context(**kw)` records its settings and returns a fake context (`push`, `no_more_inputs`, `cancel`, all recorded). The connection's `recv_bytes(timeout)` serves a queue of scripted events (chunks with base64 audio, `done`, `error`, exceptions) and raises `TimeoutError` when empty; by default it answers like the live API — one chunk per push, `done` after `no_more_inputs`, `done` after `cancel` of a context not yet done. The number of `websocket_connect` calls is counted, each manager closes its own socket, a test can close a socket from the server side, and every module a test builds is closed at teardown so no reader thread outlives its test. The tests cover:
  - config: `ConfigError` on a missing extra, a missing or empty key, a missing `voice_id`, and each invalid field (parametrized, `timeout` included; a float `sample_rate` among the rejected values); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the context settings (defaults, and `language` / `generation_config` / `max_buffer_delay_ms` only when set);
  - held socket: construction connecting once, with the connect options (`open_timeout`); a rejected key at construction as `ConfigError`, any other handshake failure as `TTSError`, the client closed either way; two utterances over one socket (one connect, fresh context ids); a server close between utterances followed by a reconnect at the next one (two connects); a socket idle past `_IDLE_REPLACE` (patched clock) replaced before the utterance's first context and never inside an utterance; one whose wall clock ran 21 s ahead of the monotonic one since its last message (the host slept) replaced, and one at 19 s kept; a reconnect failure as `TTSError` of that utterance only, the next one trying again; a blank `say` touching nothing; foreign events (chunks, `done`, `error` of a context the utterance did not open) neither fed, nor counted as unread, nor failing the utterance;
  - complete text: pushes then `no_more_inputs`; a long text sent in slices of at most 50 characters that concatenate back to it; audio reaching the callback decoded, with `flush_done`/`timestamps` skipped; the odd-byte carry-over; after `done`, no `cancel` sent and the socket left open;
  - errors: an `error` event, a connection close before `done`, and a sender failure wrapped in `TTSError`; an `error` event or a callback error leaving the socket open for the next utterance, a connection close or a stall leaving it to be replaced; callback errors propagating unchanged; cancellation mid-audio stopping callbacks before `CancelledError`, with every unacknowledged context cancelled and the socket open; an ended context left unacknowledged by a silent server as `TTSError` after `timeout`;
  - connection setup: a cancel during a held reconnect handshake surfacing within a second, the late socket closed with no context opened and the next utterance connecting again; a handshake slower than `timeout` as `TTSError`, at construction and on a reconnect;
  - text stream: word groups pushed, never an empty push; an empty stream sending nothing and returning; audio reaching the callback before the source has yielded its last piece; a source error propagating unchanged with no `no_more_inputs`; cancellation while the source is open and the server silent, surfacing promptly; a late sender of a cancelled utterance never pushing during the next one;
  - contexts (with the thresholds patched small): a segment per context, each ended and the next opened only after the previous one's `done`, the text concatenating back; an idle pause ended by the worker and acknowledged, the later text going to a second context; a late first audio on the idle context arriving, in order, before the next context is opened; an early `done` on the current context forcing rotation, with a late `error` from it ignored; a stream ending on an idle context waiting for its `done`;
  - bounded unread audio: with the bound shrunk to one chunk and the callback blocked, the sender stops short of the whole text, then pushes the rest once playback resumes;
  - `close()` closing the socket and the client, and only the client when the socket is dead.
- **Live (`tests-e2e/test_modules.py`)**: one `MODULES` row (`cartesia`, `api_key_env: "CARTESIA_API_KEY"`, the "Tessa" voice id) plus `_REQUIRED_IMPORT["cartesia"] = "cartesia"` in `support.py`. Every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is missing ([testing.md](../testing.md), "Live tier"); the scenarios that speak more than once on one module (the cancel one included) exercise the held socket. CI maps an optional `CARTESIA_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `cartesia==4.2.0`

Confirmed from the package and from live calls on 2026-10-26 (API version `2026-08-14`, the SDK's default):

- `from cartesia import Cartesia`; `Cartesia(api_key=...)` sends it as a bearer token. `client.tts.websocket_connect()` is a context manager over `websockets.sync.client.connect` to `wss://api.cartesia.ai/tts/websocket`, yielding a `TTSResourceConnection` with `context(context_id=, model_id=, voice=, output_format=, language=, generation_config=, max_buffer_delay_ms=, ...)`, `recv_bytes(timeout=)` (raises `TimeoutError`), `parse_event`, `send`, `close`. A context has `push(text)`, `no_more_inputs()`, `cancel()`, `receive()`. `voice` is a plain id string.
- `sonic-3.6`, `sonic-3.5`, `sonic-3` and `sonic-latest` all answer; `sonic-latest` currently produces the same audio as `sonic-3.6`. First audio 0.18–0.22 s after a complete sentence on `sonic-3.6` / `sonic-3` (one `sonic-3.5` call took 1.7 s). An unknown model → `error` event `Model not found` / 404 / `model_not_found`. A bad key → HTTP 401 at the handshake (`InvalidStatus`).
- Empty transcript with `continue: true` → 400 `transcript must not be empty when continue is true unless flushing`. `no_more_inputs()` alone on a fresh context → 400 `No valid transcripts passed`.
- Pauses: a push 3.5 s after the last audio is still spoken; after 10 s or 30 s the context has already sent `done` (at ~7.0 s after its last chunk) and the push is dropped silently. A second context on the same socket works.
- Close: ~1.0–1.1 s after `done`; with `cancel` first, sometimes ~0.04 s and sometimes still ~1.0 s. Mid-generation with the default `max_queue` and a slow reader: the 10 s close timeout; with `max_queue: None` and `cancel`: ~1.1 s.
- Through the module: first audio 0.22–0.24 s for a text stream, 0.38–0.68 s for a 30-sentence `say`; a text stream paused 10 s mid-answer rotates and speaks the rest; `say` returns ~0.18 s after its last chunk.
- Bounded unread audio (2026-10-27, the module through `TTSEngine` with a sink feeding in real time, the unread count sampled 20× a second): a 200-sentence `say` peaked at 38.4 s of audio over a 75 s window, a 60-sentence `say` at 36.8 s and the same text as a word-by-word stream at 38.1 s over 40 s — the lead plus one segment, a plateau, where the server (several times faster than real time) would otherwise have run minutes ahead. First audio 0.24–0.29 s, unchanged by the gate. The same day: a 10 s pause mid-stream gave two contexts, both ended and acknowledged, the rest spoken on the second; a cancel after three chunks of a 30-sentence `say` surfaced in 0.20 s, socket closed 1.05 s later, with the default receive queue; no module thread left alive afterwards.
- Held socket (2026-10-28, raw SDK probes from the development Mac, `sonic-3.6`, one short sentence per utterance, first audio timed from the push):
  - **Sequence.** Six utterances in a row on one socket, a fresh `context_id` each and no reconnect: all spoken, first audio 100–107 ms (median 103 ms), every event carrying its own context's id. Six utterances on a fresh socket each: handshake 119–160 ms (median 129 ms) plus first audio 88–102 ms, 207–263 ms in all (median 226 ms). Holding the socket cuts the time to first audio by more than half.
  - **Idle.** Sockets left idle 15, 60 and 180 s then used: alive, first audio 77–109 ms. Left idle 320, 420 and 660 s: closed by the server at 300.0–301.0 s with `1000 (OK) connection idle timeout`, with `websockets`' 20 s pings answered throughout or with pings off alike; the server never pinged. The close is a clean handshake seen by the reader as `ConnectionClosedOK`, and a send afterwards raises `ConnectionClosedOK` (the SDK's `_ensure_connected` does not reconnect, see "Dependencies"). A client message alone resets the idle clock: a socket sent only a `cancel` of an unknown context every 120 s was still open at 540 s. An active socket, one short utterance every 90 s, was still open after 21 minutes and 14 utterances, first audio 80–206 ms throughout: no session cap seen.
  - **Cancel without closing.** See "Cancellation": one to three stray chunks within 45–102 ms, then `done` for the cancelled context; the next utterance on the same socket at once, first audio 92–117 ms; the tail interleaved with the next utterance's first audio once in three runs. A cancel of an already-`done` context, or of an unknown id → 400 `error` naming that `context_id`, the socket unaffected.
  - **Errors.** An unknown model (`error` 404, one per message sent to the context) and an empty `no_more_inputs` (`error` 400) leave the socket usable: the next utterance on it had first audio in 78–86 ms. Every `error` names its `context_id`.
  - **Close.** Closing a held socket after use: 38 ms in most cases, 1.03 s after cancels and errors on it.
  - **Through the module** (the held-socket implementation, `TTSEngine` with a sink recording arrivals): construction 289 ms with its one handshake; eight `say`s in a row on that socket, first audio 92–132 ms (median 108 ms after the first), one connect in all; a cancel mid-audio, then three `say`s at once on the same socket, first audio 97–120 ms; a fresh engine per `say`, construction to first audio 196–265 ms (median 235 ms); a `say` after 305 s idle reconnected once and had first audio in 241 ms. The latency benchmark (`benchmarks/latency`, run `20261028-104645`, 20 runs per cell, `sonic-3.6`) gave mean TTFA 92 / 87 ms for `say` short / medium and 150 / 166 ms for `stream` short / medium, against 210 / 203 / 274 / 266 ms with a socket per utterance (run `20261027-145315`); `load s` rose from 0.18 to 0.29 s, now including the handshake.

## Open questions

Deferrals only (design is settled). The socket held across utterances, once question 1 ("Connection reuse"), was measured (handshake 120 ms warm, 377 ms cold, more than half the 207–263 ms first audio of a fresh socket; 100–107 ms first audio on a held one) and is now the design above.

1. **Reconnecting inside an utterance.** A text stream silent for more than 300 s mid-utterance (a very long tool call) loses its socket to the server's idle close and fails at its next push, as it did with a socket per utterance. Every context of the utterance is acknowledged at that point, so the sender could reconnect before opening the next context with nothing lost. Not done until a pause that long is seen in practice.
2. **Timestamps.** `add_timestamps` gives word timings that could drive captions; the engine has no channel for them yet.
3. **`base_url` / `websocket_base_url`** for a regional or proxied endpoint. The client takes both; not needed for the hosted API.
4. **Expiry threshold.** The 7 s expiry is measured, not documented (the docs say "1 second after the last audio output"). If Cartesia changes it, `rotate_after` moves; no config field until there is a reason. The same holds for the 300 s socket idle limit and `_IDLE_REPLACE`.
5. **Context flushing instead of rotation.** The API's `flush` (an empty push with `flush: true`, answered by `flush_done`) would acknowledge a segment without ending its context, keeping prosody continuous across segments; its rate limit is unknown. Contexts were chosen because two of them on one socket were already validated live. Measure before switching.
