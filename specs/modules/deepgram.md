---
code:
  - src/tts_engine/modules/deepgram/__init__.py
  - src/tts_engine/modules/deepgram/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_deepgram.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Deepgram Module

**Status:** Implemented

## Overview

`deepgram` implements `TTSModule` using Deepgram's hosted voices through the official `deepgram-sdk` Python package: the **Aura-2** voices on the speak v1 WebSocket, and the **Flux TTS** voices on the speak v2 WebSocket. The model picks the API (see "Two APIs"). It asks for `linear16` output, which is signed 16-bit mono PCM at the module's declared rate, so audio goes from the socket to `callback` as is: no codec and no float conversion. It is one API-backed provider among others, not the default and not part of the base install, and ships behind the `deepgram` packaging extra (see "Dependencies").

Its shape is the ElevenLabs one: a **synchronous** SDK client driven from the cancellable worker, with no private event loop. It is a **native text-stream** backend. The socket takes repeated `Speak` messages and starts speaking before the text is complete, so `synthesize_text_stream` forwards text as it arrives and does no sentence grouping. A complete text goes over the same socket in small slices (`slice_text`), so the same admission gate applies to both shapes.

Every utterance runs over **one socket the module opens at construction and holds for its lifetime** (see "One socket, held for the module's lifetime"). An utterance ends on the answer to its last `Flush`, not on a `Close`, and a cancelled one is stopped with `Clear` (v1) or `Interrupt` (v2) instead of closing the socket. The WebSocket handshake, measured at 285–450 ms, is about half of a fresh socket's time to first audio, so it is paid once and not on every `say()`.

### Two APIs

Deepgram serves two generations side by side, on two sockets with their own model ids:

- **Aura-2** (`aura-2-<voice>-<lang>`, and first-generation `aura-<voice>-en`) on `wss://api.deepgram.com/v1/speak` — `speak.v1`.
- **Flux TTS** (`flux-<voice>-<lang>`, GA since 2026-08-12) on `wss://api.deepgram.com/v2/speak` — `speak.v2`, turn-based, built for voice agents.

Each socket rejects the other's models at the handshake (HTTP 400). The module therefore needs no switch of its own: a `model` starting with **`flux-`** selects v2, anything else v1, which is how Deepgram presents them (the user picks a voice). Both are supported because both are current: Aura-2 stays available and unchanged, with no sunset announced, and is its own voice line. Following [project.md](../project.md) ("Dependency strategy for TTS backends"), the v1 branch goes when Deepgram sunsets it. The default stays `aura-2-thalia-en`: a latency comparison on 2026-10-27 found Flux slower (open question 5).

The two sockets have the same shape — `Speak` text messages, `Flush`, a cancel message, `Close`, a blocking iterator of audio and typed events — so one implementation drives both. They differ in these places:

| | v1 (Aura-2) | v2 (Flux) |
|---|---|---|
| Text message | `send_text(SpeakV1Text(text=…))` | `send_speak(SpeakV2Speak(text=…))` |
| A flushed segment is fully generated when | `Flushed` arrives (after the segment's audio) | `SpeechMetadata` arrives (after the turn's audio); v2's own `Flushed` arrives when the turn **starts**, before any of its audio |
| Cancel the text pending and being generated | `send_clear()` → `Cleared` | `send_interrupt()` → `SpeechInterrupted` (v2 has no `Clear`: a raw `{"type": "Clear"}` closes the socket with `1003 MESSAGE-0000`) |
| `Flush` rate limit | 20 per socket per 60 s, then `Warning EXCESSIVE_FLUSH` and the `Flush` is ignored | none documented, none hit (65 flushes in 40 s) |
| Character rate limit | about 2400 characters per socket within a recent window (≈ 60 s), then a `1008 DATA-0001` close | not measured; the same budget applies |
| Idle socket | alive after 600 s, with or without pings (longer not measured, and not relied on: `_IDLE_REPLACE`) | closed after 60 s with no client message (`NET-0004`); a WebSocket Ping counts, and with `websockets`' default pings it was alive after 600 s (longer not measured, and not relied on) |
| Session cap | none documented (see "Validated against") | 1 hour (`NET-0003`) |
| Errors | a close code (`1008 DATA-0001`) | an `Error` event (`code`, `description`), then a close |
| Extra config | — | `expressivity`; a wider `speed` range |

On v2, each `Flush` ends a **turn**: the server answers `SpeechStarted`, then `Flushed` as it starts generating it, then the turn's audio, then `SpeechMetadata` (duration, character counts). Turns queue: a later turn's `Flushed` comes only after the previous turn's `SpeechMetadata`. `Close` ends the session after the pending turns, with `SessionMetadata`, then the server closes. Flux joins the `Speak` texts of a turn verbatim, so the module's word groups, which keep their spaces, concatenate back to the source text.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "deepgram"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Deepgram API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Deepgram's own convention is `DEEPGRAM_API_KEY`). Lets a config file be committed with no secret. |
| `model` | non-empty string | no | `"aura-2-thalia-en"` | The model, which **is** the voice, and which picks the API ("Two APIs"): an Aura model, `aura-2-<voice>-<language>` (e.g. `aura-2-hector-fr`, `aura-2-julius-de`) or a first-generation `aura-<voice>-en`, on speak v1; or a Flux model, `flux-<voice>-<language>` (e.g. `flux-haley-en`), on speak v2. Deepgram has no separate voice parameter. |
| `sample_rate` | integer | no | `24000` | Output PCM rate. One of `8000`, `16000`, `24000`, `32000`, `48000`, the `linear16` rates the SDK documents. |
| `speed` | number | no | — (API default 1.0) | Speaking-rate multiplier: `0.7`–`1.5` with an Aura model; `0.5`–`1.5` in steps of `0.05` with a Flux model (the API rejects anything else at the handshake). Flux may still refuse some voice/speed pairs (`SPEED_NOT_SUPPORTED`); that surfaces as `TTSError`. |
| `expressivity` | integer | no | — (API default) | Flux only: how expressive the delivery is, `-2`–`2`. Set with an Aura model, it raises `ConfigError`. |
| `mip_opt_out` | boolean | no | — (API default: opted in) | Opt the requests out of Deepgram's Model Improvement Program, which affects pricing. |
| `timeout` | finite number > 0 (seconds) | no | `30` | Bounds the socket's handshake, at construction and on every reconnect (the SDK exposes no open timeout of its own, so the module polls the connect against it). Also bounds every wait for the server: a `Flush` left with no message at all for this long is a stalled request, and a cancel's `Cleared` / `SpeechInterrupted` that has not come within this long gets the socket dropped. A stall fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

### API key resolution

Same rules as ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. A non-string value raises `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` naming the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `DeepgramClient(api_key=...)`. The module never relies on the SDK's own `DEEPGRAM_API_KEY` fallback.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `model`: a non-empty string (defaulted when absent).
- `sample_rate`: if present, an integer in `{8000, 16000, 24000, 32000, 48000}`. Booleans are rejected rather than treated as integers.
- `speed`: if present, a number (not a bool) in `[0.7, 1.5]` with an Aura model, or in `[0.5, 1.5]` and a multiple of `0.05` with a Flux model.
- `expressivity`: if present, a Flux model, and an integer (not a bool or float) in `[-2, 2]`.
- `mip_opt_out`: if present, a boolean.
- `timeout`: if present, a finite number (not a bool) `> 0`.

`speed`, `expressivity` and `mip_opt_out` are **left out of the connection when unset**, so the API's own defaults apply. Whether a `model` exists, and whether the key is valid, is only known to the API, and the API answers at the handshake. Because `__init__` opens the socket (see "One socket, held for the module's lifetime"), a handshake **rejected** with HTTP 400 (a parameter the API refuses), 401 (a bad key) or 403 (an unknown model or voice) raises **`ConfigError` at construction**, naming the status. That is the same point where a local module rejects an unknown model name. A Flux model on v1 or an Aura model on v2 cannot happen, since the prefix picks the API. Any other failure to open the socket is a `TTSError` at construction: a network error, a handshake slower than `timeout`, or another HTTP status (429, 5xx). The same rejection on a later reconnect, for example a key revoked mid-life, is a `TTSError` of that utterance.

## Implementation notes

### SDK entry point

```python
from deepgram import DeepgramClient
from deepgram.speak.v1.types import SpeakV1Text

client = DeepgramClient(api_key=key)
manager = client.speak.v1.connect(
    model=self._model,
    encoding="linear16",
    sample_rate=self._sample_rate,
    # speed=..., mip_opt_out=... only when set
)
socket = manager.__enter__()  # V1SocketClient over a websockets.sync connection
socket.send_text(SpeakV1Text(text="Hello "))
socket.send_flush()            # ... utterance over on the matching `Flushed`
socket.send_clear()            # cancel: everything until `Cleared` is stale
for message in socket:         # the reader thread: bytes (audio) or a typed event
    ...
manager.__exit__(None, None, None)  # close(): the WebSocket close handshake
```

With a Flux model the call is `client.speak.v2.connect(...)` (same keywords, plus `expressivity`), which yields a `V2SocketClient`. Text goes through `socket.send_speak(SpeakV2Speak(text=...))`, and a cancel through `send_interrupt()`. `send_flush`, `send_close` and the iterator are the same.

One client is built in `__init__` and reused, and so is one socket (next section). `connect(...)` is a context manager over `websockets.sync.client.connect`. The module enters it through `open_cancellable` so the handshake honors `stop` and `timeout`, and leaves it only when the socket is replaced or the module closes.

Messages from the server are either `bytes` (audio) or typed events, and the module dispatches on the event's `type` string, not its class. The SDK parses `Cleared` into the `SpeakV1Flushed` class, so `isinstance` would mislabel it. The `sequence_id` of v1's `Flushed` and `Cleared` is not relied on: it counts per socket, in a way the documentation does not describe. Message order alone delimits an utterance (see "Cancellation").

| `type` | API | Meaning | Module action |
|---|---|---|---|
| (bytes) | both | PCM audio | fed to `callback`; dropped while a cancel is settling |
| `Metadata` | v1 | request id, model | ignored |
| `Flushed` | v1 | every text sent before the matching `Flush` has been spoken | counted as a finished segment (see "Pending-text backpressure"); dropped while a cancel is settling |
| `Cleared` | v1 | answer to `Clear`: the text pending and being generated is gone | settles the cancel (see "Cancellation") |
| `Connected` | v2 | request id, resolved model | ignored |
| `SpeechStarted` | v2 | a turn has begun | ignored |
| `Flushed` | v2 | the server has started the flushed turn; its audio follows | ignored |
| `SpeechMetadata` | v2 | the turn is complete: every text of it has been spoken | counted as a finished segment; dropped while a cancel is settling |
| `SpeechInterrupted` | v2 | answer to `Interrupt`: the turn and any unflushed text are gone (it replaces that turn's `SpeechMetadata`) | settles the cancel |
| `SessionMetadata` | v2 | session totals, after `Close` | ignored |
| `Warning` | both | e.g. `EXCESSIVE_FLUSH` (v1), `NO_SYNTHESIZABLE_TEXT`, `SYNTHESIS_RETRYING`, `INTERRUPT_IN_PROGRESS`, `NO_AUDIO_GENERATED` (v2) | logged at `WARNING`; `EXCESSIVE_FLUSH` also ends the socket's use (see "Flush and character budgets"); an Interrupt-scoped warning while a cancel is settling settles it (the server ignored the `Interrupt`, nothing is generating) |
| `Error` | v2 | `code`, `description` (e.g. `DATA-0002`, `NET-0004`) | `TTSError` during an utterance; logged at DEBUG while idle (a close follows, and the socket is dead) |

### Output format and sample rate

`encoding="linear16"` is headerless signed 16-bit little-endian mono PCM at exactly `sample_rate` (confirmed live: 1920-byte messages at 24 kHz, i.e. 40 ms frames). That matches the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with **no conversion in the module**. The worker feeds the messages through the shared `PCMFeeder` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers"). An odd trailing byte is carried over to the next message, so `callback` only ever gets whole samples (the API has not been seen to split a sample). A message is handed over in slices of at most 100 ms, with `stop` polled between them. One feeder spans one utterance. `sample_rate` returns the configured value and is fixed for the module's lifetime.

### One socket, held for the module's lifetime

The module opens **one** socket in `__init__` and keeps it until `close()`. Every utterance's text runs over it, one utterance after another; the engine's say lock serializes them. The engine's contract already makes construction the expensive step (the local modules load their weights there, for seconds), so the handshake belongs there too. No utterance pays it, the very first included, and a server built at startup (the MCP server constructs the engine before it listens) answers its first request as fast as its hundredth.

The `openai` module ([openai.md](openai.md)) connects in `__init__` because its setup round trip is where the server validates the key, model and voice. Deepgram's handshake does the same job, with HTTP 401, 403 or 400, so the reasoning carries over unchanged. Connecting lazily on the first `say` would move that `ConfigError` to the first synthesis and charge the first utterance the handshake, for no gain. The cost of connecting at construction is that it needs the network: an unreachable API fails construction as `TTSError`, as for `openai`.

**Setup.** At construction and on every reconnect, the setup is the same, and bounded by `timeout`. The module runs the handshake (`connect(**kwargs).__enter__` through `open_cancellable`, so a worker-side reconnect still honors `stop`), then starts the socket's reader thread. There is no further setup message: the handshake's success is the socket's readiness.

**Per utterance.** The worker takes the socket after these checks, run in order:

1. **Settling cancel → wait.** If the previous utterance was cancelled and its `Cleared` / `SpeechInterrupted` has not arrived yet, wait for it, polling `stop`, for at most `timeout`. The measured wait is about 0.1 s on v1 and 0.11–0.18 s on v2, and is only met when a `say` follows a cancel at once. If the answer has not come by then, the socket is dropped (step 2 then reconnects).
2. **Dead socket → reconnect.** The socket is dead once its reader has ended, which happens when the server closes it: a Flux idle close, the session cap, a `1008`/`1011` close, the host slept. It is also dead once a send on it has failed, or once it was dropped. The worker runs the setup above, and the utterance pays one handshake. A failure to reconnect fails this utterance as `TTSError`, and the next utterance tries again. Nothing retries within an utterance: a socket that dies mid-utterance fails that utterance as `TTSError`, as before.
3. **Old, idle or flush-spent socket → replacement.** The socket is replaced before the utterance's first text in any of four cases, and the old one is closed off the worker:
   - **Age.** It is older than **55 minutes** (`_ROTATE_AFTER`), under Flux's documented one-hour session cap (`NET-0003`). The same age applies on v1, where no cap is documented, so that both APIs share one rule. It costs one handshake per hour of use.
   - **Idle.** It has carried no message, sent or received, for more than **540 s** (`_IDLE_REPLACE = 9 * 60.0`). That is inside the 600 s verified alive on both v1 and v2. Deepgram documents no idle limit beyond Flux's ping-reset 60 s window. The module therefore never relies on an unmeasured server idle cutoff: idle beyond 600 s was not measured, and it does not matter, because no socket idle that long is used. This is Cartesia's `_IDLE_REPLACE`, in the same check order: dead → reconnect, then idle too long → replace ([cartesia.md](cartesia.md), "One socket, held for the module's lifetime"). After nine idle minutes one handshake is a negligible price. A half-open socket after a long host sleep is replaced the same way, once the sleep is seen (next point).
   - **The host slept.** The host slept for more than 20 s since the socket's last message. The idle time is measured on both clocks, and the larger is taken. `time.monotonic()` stops while the host sleeps: on macOS it is `mach_absolute_time()`, and on Linux `CLOCK_MONOTONIC` excludes suspend. The wall clock (`time.time()`) keeps running. A wall-clock idle time exceeding the monotonic one by more than 20 s (`_SLEPT`, one ping interval) means a sleep long enough to miss a keepalive ping. Over such a sleep a Flux socket has outlived its 60 s inactivity window on the server, and either socket may be half-open. A backward wall-clock step (NTP) only lowers the wall figure and is outweighed by the monotonic one.
   - **Flush or character budget.** On v1, it has spent its flush budget; on either API, its character budget (next section).

A blank complete text (empty or whitespace only) returns before any check and touches nothing. A text stream runs the checks when its worker starts, before its first piece arrives, so a reconnect overlaps the wait for the source's first token.

**Idle.** Between utterances nothing is sent. No application-level keepalive exists for TTS: a `{"type": "KeepAlive"}` text closes the socket (`1008 DATA-0000` on v1, `1003 MESSAGE-0000` on v2). What keeps an idle Flux socket open is the WebSocket **Ping** that `websockets`' sync client sends every 20 s by default (`ping_interval=20`, which the SDK leaves at its default), and that Deepgram documents as counting against its 60 s inactivity window. Measured: without pings, Flux closes an idle socket after 59 s (`Error NET-0004`, then a `1011` close); with them it was alive after 600 s. v1 survived 600 s idle with or without pings. The module configures no pings of its own. If a `websockets` version without sync keepalive ever lets the Flux socket idle out, the dead-socket check reconnects it, which is today's cost and not a failure. Unlike Cartesia, where pings do not count, the module needs no keepalive traffic of its own, and the idle replacement above is a margin rather than the server's limit. The reader keeps draining the socket while it is idle. Nothing is expected then except a server `Error` before a close (logged at DEBUG), or the stale tail of a cancel, which is dropped.

**A dead socket, and how it is seen.** Every way a socket dies ends the reader's iteration, and the reader marks the socket dead with the cause:

- a server close, seen as `ConnectionClosedError`: `1011 NET-0004` after Flux's idle window, `1011 NET-0003` at its session cap, `1008`/`1003` after a message the server refuses, each preceded on v2 by an `Error` event;
- a clean close, seen as the end of the iteration;
- a transport error.

A send on a dead socket raises (`ConnectionClosed`) and marks it dead too.

A **half-open** socket is the hard case: the host slept, or the network dropped without a FIN, and the client still believes the connection is open. It is caught three ways:

1. **Before the utterance**, by the slept-host and idle rules above.
2. **While idle**, by `websockets`' keepalive. A Ping unanswered within `ping_timeout` (20 s) closes the connection with `1011 keepalive ping timeout`, which ends the reader, at most about 40 s after the link went.
3. **During an utterance**, by the same keepalive or by the stall rule ("Error handling"). A `Speak` written into a half-open socket usually does not fail at once, since the kernel buffers it. The utterance then fails as `TTSError` once the keepalive closes the socket or `timeout` passes with a `Flush` unanswered, whichever comes first, and the next utterance reconnects.

Nothing retries within an utterance, as in `openai` and `cartesia`.

### Flush and character budgets

v1 accepts at most **20 `Flush` messages per socket in any 60 s**. This is documented and was measured: the 21st within the window is answered `Warning EXCESSIVE_FLUSH` and ignored, with no `Flushed`, and flushes are accepted again once the first one is 60 s old. Refused flushes and `Clear`s do not count, `Clear` does not reset the count, and a new socket starts with a fresh budget. With a socket per utterance this limit was out of reach, since an utterance flushes once per segment of at least 600 characters. On a held socket, **twenty short `say()` calls in a minute** — an embedder speaking sentence by sentence — would hit it. The utterance whose `Flush` was refused would never see its `Flushed` and would stall for `timeout`.

The module therefore keeps the send times of the socket's flushes. At an utterance's start, a v1 socket with **16** or more flushes (`_FLUSH_BUDGET`) sent in the last 60 s is rotated (step 3 above). The four left over cover the utterance's own flushes inside the window: a segment is at least 600 characters, about 40 s of speech, and the next one is admitted only once that one is generated and playback has caught up to the 10 s lead, so one utterance sends at most a few flushes a minute. The cost is one handshake per 16 short utterances, and only while they come faster than 16 a minute. v2 has no such budget: Flux took 40 sequential one-word utterances and then 25 flushes at once with no warning.

If `EXCESSIVE_FLUSH` arrives during an utterance anyway (the budget model no longer matches the server), the socket is marked **exhausted**, so the next utterance reconnects. The current utterance is then handled one of two ways:

- If its **final** `Flush` was the refused one (all its text has been sent), the worker sends `Close`. The server speaks the text still pending and closes the socket: measured, the remaining audio, then a clean close 0.15 s after `Close`. The utterance ends normally on that close, as an utterance ending on `Close` always did.
- If a mid-utterance `Flush` was refused, the sender is stuck behind the segment gate with no answer coming. The utterance fails as `TTSError("… flush rate limit exceeded")` and the socket is dropped.

The text of a refused `Flush` that ends a sentence is spoken anyway. Only the acknowledgment is missing.

**Character budget.** A socket also refuses text once about **2400 characters** have been submitted on it within a recent window. Deepgram documents the error as `1008 DATA-0001`, "too many characters submitted within a recent time window", and the server closes the socket. This was measured with back-to-back 212-character utterances on one `aura-asteria-en` socket, each waiting for its `Flushed`:
- at 73 characters a second, the socket closed after 2544 characters (32 s);
- at 36 characters a second, it took 12 720 characters over 352 s without an error;
- a fresh socket opened right after the close worked at once.

Playback paces the text to speech, about 1000 characters a minute, so a module feeding a real-time sink never reaches the limit. A sink faster than real time with a fast model does reach it across utterances: Aura-1 generates at 20–100× real time. The latency benchmark's instant sink lost 4 of 48 `aura-asteria-en` syntheses to `DATA-0001`, which a socket per utterance had never met.

The module therefore also logs the characters it sends on the socket. At an utterance's start, a socket with **1200** or more characters (`_CHAR_BUDGET`) sent in the last 60 s (`_CHAR_WINDOW`) is replaced, on both APIs. That is half the measured limit, which leaves the utterance room for more than one 900-character segment. The limit on Flux was not measured; the budget costs a handshake only under a faster-than-real-time sink, so it is kept there too. With the budget in place, the same benchmark entry ran its 96 syntheses with no `DATA-0001`. A single utterance that alone submits more than the limit within a minute (a long text, a fast model, an instant sink) is not covered. That is the same as with a socket per utterance, and the segment gate paces it to generation.

### Threads: sender, reader, worker

The SDK socket is synchronous, and its `recv` and iterator have **no timeout**. A worker blocked in `recv` while the server is silent (an LLM pausing mid-answer) could not see `stop`. The module therefore splits the work over three threads that share the socket (websockets' sync connection allows sends concurrent with receives). Their lifetimes differ: there is one reader per **socket**, and one sender and one worker per **utterance**.

- **Reader** (daemon, started by the setup, living as long as its socket). It iterates the SDK socket. Each message goes to the utterance currently **attached** to the socket. The reader counts that utterance's unread audio bytes (`unread`), notes when the server last spoke, and counts the finished-segment answers itself (`Flushed` on v1, `SpeechMetadata` on v2), so the sender's gates (below) open even while the worker is busy feeding audio. It then puts the message on the utterance's inbox. While a cancel is **settling** it drops every message and watches for the cancel's answer. No utterance is attached then, since the next one waits for the answer before attaching, so nothing of the aborted utterance can reach it. When the iteration ends, or raises, the reader marks the socket dead with the cause, and hands the attached utterance (if any) the exception or an end marker.
- **Worker** (from `run_cancellable_worker` / `run_text_stream_worker`). It runs the per-utterance checks and attaches a fresh utterance state to the socket. Then it starts the sender and takes messages from the inbox with a **100 ms timeout**. On every wake it checks `stop`, the sender's error and the stall rule, and it calls `callback` for audio, subtracting what it feeds from the unread count. It is the only thread that calls `callback`, so calls never overlap. On the way out it detaches the utterance, and on an aborted one it starts the cancel (see "Cancellation").
- **Sender** (daemon). It forwards text, then the utterance's final `Flush` (see the next sections). Every send on the socket — the sender's `Speak` and `Flush`, the worker's `Clear` / `Interrupt` and `Close` — goes through one per-socket **send lock**, and the sender checks `stop` inside that lock before each send. Once the worker has set `stop` and sent the cancel message, no text of the aborted utterance can follow it onto the socket, where its audio would come after the cancel's answer and be taken for the next utterance's.

`synthesize_text(text)` runs this same machinery with `slice_text(text, 50)` as the piece source: the complete text goes in verbatim slices of at most 50 characters (cut at a sentence end, else at whitespace), so the admission gate has a grain. The two input shapes differ only in where the pieces come from.

### Text stream (native)

`synthesize_text_stream` is a **true stream-to-stream** pass. The live API starts producing audio before any `Flush`: words streamed one per message with 100 ms gaps gave first audio after 0.3 s, and only a trailing fragment with no sentence end waited for the `Flush`. The sender:

1. pulls the bridge's `pieces` and groups them at **word boundaries** with `split_at_last_space`, so a sub-word LLM token never reaches the server alone; each group is one `Speak` message;
2. cuts the text into **segments**, each ending with a `Flush` (next section);
3. at end of stream, sends the held-back remainder, then a final `Flush` if anything is pending. It sends no `Close`: the socket stays open for the next utterance. An empty stream sends nothing.

If `stop` ends `pieces` early, the sender sends nothing more.

### Pending-text backpressure

The server rejects an utterance once about **2000 characters** of text are waiting to be spoken. Live, it either closes the socket with `1008 DATA-0001` after about 2000 characters of audio, or (with one word per message) stops answering until the keepalive times out. A fast LLM easily outruns synthesis (which runs at about 2× real time), so long answers would fail without flow control. `Flush` is the only acknowledgment the protocol offers, and on v1 it is **rate-limited** to 20 per 60 s (see "Flush and character budgets"). A `Flush` per sentence is therefore not an option.

The module bounds the pending text with **flushed segments, one at a time at the server**:

- A segment is flushed at the first sentence end once it holds **600** characters, or at a word boundary at **900** characters at the latest. Text longer than the room left in a segment is cut by `cut_at_boundary`: at its last sentence end within that room, else at its last whitespace, else hard at the limit (a single "word" longer than a segment).
- The first text of a new segment is sent only once the previous segment is **finished** — answered by `Flushed` on v1, by `SpeechMetadata` on v2 (see "Two APIs") — i.e. once the server has **generated** all of it. The sender polls `stop` every 100 ms while it waits.

The server therefore never holds more than one segment (≤ 900 characters) of unspoken text, and the audio it has generated ahead of playback is bounded by that segment (see the next section). Segments are at least 600 characters (about 40 s of speech), so one utterance's flush rate stays far under the limit. Playback is not interrupted: generation runs at 2× real time and the module keeps a 10 s lead, so `Flushed` for segment *n* arrives while segment *n* is still playing, and segment *n+1* is admitted long before the audio runs out. The first version allowed one unanswered `Flush`, i.e. two segments at the server. It was validated live then: a 2925-character stream sent one word at a time in about 1 s gave 170 s of continuous audio with no error, where the same stream without the gate failed with `DATA-0001`.

A complete text goes through the same segmentation, so `say` with a text longer than 2000 characters also works.

**On Flux** the same segmentation and gate apply, with `SpeechMetadata` as the finished-segment answer: a segment is one turn, and at most one turn is at the server unfinished. Flux showed neither of v1's two limits in what was measured. A 900-character turn generated in full (57 s of audio), and neither 30 one-sentence turns flushed back to back nor 25 flushes at once drew a warning. Neither limit is documented as absent, though, and one segment per turn keeps the audio bound below identical for both APIs. So the thresholds are shared rather than tuned per API (open question 4).

### Bounded unread audio

The server generates at about 2× real time while the default sink feeds at 1×. Without a second gate, the audio read and not yet fed would grow with the utterance's length (about 48 kB per second of audio ahead at 24 kHz). The sender therefore **admits** a `Speak` message only while the unread audio is under **10 s** at the configured rate (`_MAX_UNREAD_SECONDS`). The reader counts bytes in, the worker counts bytes out, and the sender waits on the shared condition, polling `stop`, until playback has caught up. Text admission is thereby coupled to playback progress, which also back-pressures the text bridge and, through it, the LLM.

The count belongs to **the utterance**, not to the socket: each utterance starts at zero, with its own inbox. A completed utterance ends with nothing unread (the worker fed all of it). An aborted one leaves its inbox behind with its state, and the reader drops the cancelled audio still arriving before the cancel's answer instead of counting it. So no utterance's gate is charged with another's audio, and the server holds nothing of a cancelled utterance once its `Clear` / `Interrupt` is answered.

What this bounds: the unread audio never exceeds the lead plus the audio of the text already admitted, and the latter is at most one segment (previous section) — in the order of a minute for 900 characters, not of the utterance. What it does not change: the initial lead is built as fast as the server generates, so first audio is as prompt as before. The rule is the shared one in [tts-module-interface.md](../tts-module-interface.md), "Bounded unread audio". Measured values are under "Validated against".

### End of utterance

The final `Flush` ends the utterance's text. The worker stops as soon as three things hold: the final `Flush` has been sent, every segment has been answered as finished, and the inbox is empty. The socket is then idle and stays open. An utterance that ends with `Close` instead (the `EXCESSIVE_FLUSH` fallback above) ends normally on the server's close after the remaining audio, and leaves the socket dead.

A server close **during** an utterance, without a `Close` sent, means audio was lost. It raises `TTSError` and marks the socket dead, and the next utterance reconnects. A connection error once the last finished-segment answer has arrived is not a failure (nothing was lost); the socket is dead all the same.

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"). The worker sees `stop` within one 100 ms poll (sooner when audio is flowing), stops calling `callback` and leaves its loop. It then sets `stop` itself, which releases a sender waiting on text, on a finished segment or on the admission gate. The coroutine re-raises `CancelledError` once the worker thread has exited.

**Cancel without closing.** An utterance that ends with work still at the server is **aborted**. That covers a cancel, a callback error, a text-source error, and a sender error on a socket still alive. On an aborted utterance the worker does three things, in order:

1. it sets `stop`, so the sender sends nothing more, and detaches the utterance from the socket, marking the socket **settling**;
2. it sends `Clear` (v1) or `Interrupt` (v2, with no `playback_offset`) under the send lock;
3. it returns at once. It does not wait for the answer and does not close the socket.

The reader drops every message (audio, finished-segment answers, warnings) until the cancel's answer arrives: `Cleared` on v1, or `SpeechInterrupted` on v2 (or an Interrupt-scoped `Warning`, which means the `Interrupt` was ignored with nothing generating). Then it marks the socket settled. Message order makes this exact. Cartesia filters a cancelled utterance's tail by `context_id` instead, because its tail was seen interleaving with the next utterance's audio. That is not possible here: Deepgram's audio arrives as bare binary frames, with no id on either API (v2's `speech_id` is on the events only). It is not needed either, because the cancel's answer was always the last message of the cancelled work. Measured live, the cancelled utterance's audio kept arriving for up to 0.1 s after `Clear` and 0.12 s after `Interrupt`, always **before** the answer and never after it. A `Flush` pending at the cancel is never answered: its `Cleared` / `SpeechInterrupted` replaces the `Flushed` / `SpeechMetadata`. Text sent but not yet flushed is discarded too, on both APIs. The next utterance starts on the same socket once the socket has settled ("Per utterance", step 1). Measured: its first audio came 165 ms after its first text on v1 and 236–269 ms on v2. Reconnecting instead costs 656–665 ms (a 450 ms handshake that day, plus first audio).

The socket is **dropped** rather than reused when its state is unknown: a cancel whose answer has not come within `timeout`, a failed `Clear` / `Interrupt` send, a stall (below), or a refused mid-utterance `Flush`. Dropping closes the socket off the worker and sets nothing in its place, so the next utterance reconnects. An utterance that completed normally sends no cancel.

**During the handshake** (a reconnect). `speak.v1.connect` (and `speak.v2.connect`) blocks in `websockets`' connect and the SDK exposes no open timeout, so the worker enters it through `open_cancellable`. The connect runs on a helper thread while the worker polls `stop` every 100 ms against `timeout`. A cancel during the handshake surfaces within one poll with nothing sent. The connection, if it completes afterwards, is closed on the helper thread, and the module holds no socket, so the next utterance reconnects. A handshake slower than `timeout` is `TTSError("not open within …")`.

**Closing.** A socket being replaced (dead, rotated, dropped) and the held one on `close()` are closed **off the calling thread**, on a daemon thread, and a close failure is logged at DEBUG. A socket still generating used to hold the close handshake for 4–9 s (2026-10-26); on 2026-10-28 it took 0.10–0.15 s, generating or not. Closing never needs to be waited for, and it is bounded by `websockets`' `close_timeout` (10 s) in any case.

### Dependencies and lazy import

Requires the `deepgram` extra: `pip install tts-engine[deepgram]` / `uv sync --extra deepgram`, declared in `[project.optional-dependencies]` as `deepgram = ["deepgram-sdk>=7.15,<8"]` and added to the `all` aggregate. The `<8` pin is there because the SDK is Fern-generated and the module relies on the 7.x `speak.v1` and `speak.v2` socket clients. The SDK is pure Python (`httpx`, `pydantic`, `websockets>=12`), so it is a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `deepgram/module.py` must **not** import `deepgram` at file top. The import happens inside `__init__`, before any other config field is read; only `model` is looked at first, to pick the message type of its API:

```python
try:
    from deepgram import DeepgramClient

    if model.startswith("flux-"):
        from deepgram.speak.v2.types import SpeakV2Speak as speak_type
    else:
        from deepgram.speak.v1.types import SpeakV1Text as speak_type
except ImportError as exc:
    raise ConfigError(
        "The 'deepgram' module requires the deepgram extra: pip install tts-engine[deepgram]"
    ) from exc
```

`tests/modules/test_lazy_imports.py` adds `deepgram` to the poisoned modules and the `deepgram` row to its construction loop. The import fails before any connect, so that test needs no network. Importing the SDK triggers two `websockets` deprecation warnings (its fallback imports of the legacy client and `InvalidStatusCode`). They are upstream and harmless, and are filtered in `pyproject.toml`'s pytest `filterwarnings`, scoped to those messages.

### Error handling

- A handshake rejected with HTTP 400, 401 or 403 at **construction** is `ConfigError(f"Deepgram module: handshake rejected: HTTP {status}")`, chained (see "Validation"). `websockets` raises it as `InvalidStatus`, with the status on `exc.response.status_code`; the SDK's own `ApiError` mapping does not catch the current `websockets` exception class.
- Any other failure of the SDK or the socket is re-raised as `TTSError(f"Deepgram request failed: {exc}")`, chained via `raise ... from exc`. That covers a handshake rejected on a reconnect, a network error at construction, a server close with an error code (`1008 DATA-0001`), a keepalive timeout, and a failed send. A v2 `Error` event during an utterance is `TTSError(f"Deepgram request failed: {code} {description}")`. A server close during an utterance is a `TTSError` of its own. Every one of them leaves the socket dead, and the next utterance reconnects.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as Deepgram request failures, because `callback` is called outside the provider `try`. The utterance is aborted (`Clear` / `Interrupt`) and the socket kept.
- A text-stream failure (the async source raising) aborts the utterance the same way and propagates unchanged through `run_text_stream_worker`.
- A **stall** is `TTSError("Deepgram request failed: no answer within … s")` and drops the socket. A stall means a `Flush` sent, its segment not answered as finished, and no message of any kind from the server for `timeout` seconds. A pause in the text stream is not a stall: nothing is pending then. A handshake slower than `timeout` is a `TTSError` too (see "Cancellation").
- `EXCESSIVE_FLUSH` during an utterance is handled under "Flush and character budgets": `Close` and a normal end, or a `TTSError`.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.
- Other `Warning` events are logged, not raised.

### Lifetime

The `DeepgramClient` and the socket are both built in `__init__` ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"). `TTSModule.close()` releases the socket: it detaches it from the module and closes it off the calling thread (the WebSocket close handshake; no `Close` message, since the socket is idle). `DeepgramClient` exposes no close and holds only a lazily opened HTTP pool, which goes with the module. `close()` is called with no utterance in flight (the engine takes its say lock first). A module whose socket was dropped at `close()` has nothing to close; a dead one's manager is still exited, which is harmless. The reader of a closed socket ends with it; a sender or closer thread still finishing holds nothing of the module's.

A held socket occupies one of the account's concurrent Deepgram connections for the module's lifetime, idle or not, where a socket per utterance held one only while speaking.

## Module ID

Registered in `REGISTRY` as `"deepgram"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_deepgram.py`)**: `deepgram`, `deepgram.speak.v1.types` and `deepgram.speak.v2.types` are **faked** in `sys.modules`. The fake `DeepgramClient` has `speak.v1.connect` and `speak.v2.connect`, which record their kwargs and which API was called, count the connects, and return a context manager over a fake socket. That socket records what is sent (and through `send_text` or `send_speak`) and answers like the live API: audio per `Speak`; per `Flush`, `Flushed` on v1, or on v2 `Flushed` at once and then `SpeechMetadata` (the finished-segment answer held back until released, on both); per `Clear` / `Interrupt`, optionally some stale audio and then `Cleared` / `SpeechInterrupted` (also holdable); an `EXCESSIVE_FLUSH` warning past a configurable flush count; a server close on demand (dead socket while idle), and after `Close`. It can also run a fixed script of messages and exceptions. The tests cover:
  - config: `ConfigError` on a missing extra, on a missing or empty key, and on each invalid field (parametrized, `timeout` included; a float `sample_rate` (`24000.0`) is among the rejected values); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the connect kwargs (defaults, and `speed`/`mip_opt_out` only when set);
  - construction: one connect in `__init__`, before any `say`; a handshake rejected with HTTP 400, 401 or 403 as `ConfigError` naming the status; another handshake failure and a handshake slower than `timeout` as `TTSError`;
  - complete text: the order text, `Flush`, with no `Close`; audio reaching the callback unchanged, with `Metadata` skipped and `Warning` logged; the odd-byte carry-over; a long text sent in slices of at most 50 characters that concatenate back to it, cut into flushed segments of at most 900 characters, each ending at a sentence end; an over-long word hard-cut; the sender sending nothing of the next segment until `Flushed` arrives; a blank text sending nothing;
  - held socket: two utterances over one connect, each ending on its own finished-segment answer; a socket closed by the server while idle reconnected by the next utterance (a second connect), never inside an utterance; a reconnect failure as `TTSError` of that utterance only, the next one trying again; a rotation once the socket is older than 55 minutes, a replacement once it has been idle past 540 s, and one once the wall clock has run more than 20 s ahead of the monotonic clock since its last message (the host slept), each by moving the socket's open and last-message stamps back (no patched clock), and never inside an utterance; the stamps refreshed by both a send and a received message; flushes older than 60 s not counted; a v1 rotation once 16 flushes fall in the last 60 s, and none on v2; a rotation on either API once 1200 characters fall in the last 60 s, older ones not counted, and the text sent counted; a cancel during a reconnect's held handshake surfacing within a second, the late socket closed with nothing sent;
  - cancellation: a cancel mid-audio stopping callbacks before `CancelledError`, sending `Clear` (v1) / `Interrupt` (v2) and **not** closing the socket; stale audio and a stale finished-segment answer arriving before `Cleared` / `SpeechInterrupted` never reaching the next utterance's callback or counters; the next utterance waiting for a held answer, then starting on the same socket; an answer that never comes dropping the socket after `timeout` (the next utterance reconnects); no text sent after the cancel message (the sender racing the cancel); a callback error propagating unchanged with `Clear` sent and the socket kept;
  - flush budget: `EXCESSIVE_FLUSH` on the final `Flush` → `Close` sent, the utterance's audio complete, the next utterance reconnecting; on a mid-utterance `Flush` → `TTSError`, socket dropped;
  - bounded unread audio: with the bound shrunk to one chunk and the callback blocked, the sender stops short of the whole text, then sends the rest once playback resumes; each utterance's unread count starting at zero after an aborted one; a `Flush` left unanswered by a silent server as `TTSError` after `timeout`, the socket dropped;
  - errors: mid-stream and send failures wrapped in `TTSError` and marking the socket dead; a server close during an utterance as `TTSError`; a v2 `Error` event while idle logged, not raised, the socket then dead;
  - Flux: a `flux-` model connecting on v2 with its kwargs (`speed`, `expressivity`) and sending through `send_speak`, the word groups concatenating back to the source; an Aura model on v1 through `send_text`; the utterance running past v2's early `Flushed` until `SpeechMetadata`, feeding the audio in between; the segment gate and segmentation tests run on both APIs; a v2 `Error` event as `TTSError` with its code, a v2 `Warning` logged; validation of the Flux `speed` range and step and of `expressivity` (including set with an Aura model);
  - text stream: word groups, then `Flush`, no `Close`; an empty stream sending nothing; audio reaching the callback before the source has yielded its last piece; a source error propagating unchanged with `Clear` / `Interrupt` sent and the socket kept; cancellation while the source is open and the server silent, surfacing promptly;
  - `close()`: the held socket closed off the calling thread; nothing to close after a dead socket.
- **Live (`tests-e2e/test_modules.py`)**: two `MODULES` rows on the same key, `deepgram` (`aura-2-thalia-en`, speak v1) and `deepgram-flux` (`flux-haley-en`, speak v2), plus `_REQUIRED_IMPORT["deepgram"] = "deepgram"` in `support.py`. Every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is missing ([testing.md](../testing.md), "Live tier"). Every scenario constructs a module, so the construction-time connect is exercised on both APIs. A cancel-then-speak scenario (`test_module_say_after_cancel`: cancel a `say` mid-audio, then `say` a short text on the same engine and assert it completes with audio) exercises the held socket's `Clear` / `Interrupt` and reuse live. CI maps an optional `DEEPGRAM_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `deepgram-sdk==7.15.0`

Aura-2 (`speak.v1`), confirmed from the package and from live calls on 2026-10-26:

- `from deepgram import DeepgramClient`; `DeepgramClient(api_key=...)`. Without `api_key` it reads `DEEPGRAM_API_KEY` (not relied on). `client.speak.v1.connect(*, encoding, mip_opt_out, model, sample_rate, speed, authorization, request_options)` is a `@contextmanager` over `websockets.sync.client.connect` (default `ping_interval=20`, `close_timeout=10`) to `wss://api.deepgram.com/v1/speak`. It yields a `V1SocketClient` with `send_text(SpeakV1Text)`, `send_flush()`, `send_clear()`, `send_close()`, `recv()` (no timeout) and a blocking iterator of `bytes | SpeakV1Metadata | SpeakV1Flushed | SpeakV1Cleared | SpeakV1Warning`. `speak.v1.types.SpeakV1Text(text=...)` has `type="Speak"`.
- `linear16` at 24 kHz arrives as 1920-byte messages. First audio comes 0.13–0.3 s after the first text, and synthesis runs at about 2× real time.
- Audio starts **before** `Flush`; only text after the last sentence end waits for it. `Flush` → `Flushed` (with a `sequence_id`); `Clear` → `Cleared` within about 0.1 s; `Flush` + `Close` → remaining audio, `Flushed`, then a clean server close.
- More than about 2000 characters pending → `1008 DATA-0001` close (or, with one word per message, a stalled server). More than about 20 quick flushes → `Warning EXCESSIVE_FLUSH` (pinned down on 2026-10-28, below).
- Handshake rejections: bad key → HTTP 401, unknown model → HTTP 403, `speed` 0.5 or 2.0 → HTTP 400, `sample_rate` 22050 → HTTP 400. All are raised as `websockets.exceptions.InvalidStatus` (re-checked 2026-10-28: the SDK's `ApiError` wrapper does not fire). `speed` 0.7 and 1.5 are accepted. `sample_rate` 44100 is also accepted, but the module sticks to the documented set.
- The client's close handshake on a socket still generating blocked until generation ended (8.6 s measured for an 8-sentence text cancelled after 1.5 s). On 2026-10-28 the same abort closed in 0.15 s, with or without a `Clear` first.
- Bounded unread audio (2026-10-27, the module through `TTSEngine` with a sink feeding in real time, the unread count sampled 20× a second): a 200-sentence `say` peaked at 32.1 s of audio over a 75 s window and 32.5 s over 130 s. That is a plateau at the lead plus one segment, where the server at 2× real time would otherwise have been a minute ahead; with the earlier two-segment gate the same probe peaked at 54.6 s over 40 s. The client side of the socket is quiet for up to a minute at a time while the gate holds text, with no idle close from the server. First audio 0.5–0.7 s, unchanged by the gate.

Flux TTS (`speak.v2`), confirmed from the package and from live calls on 2026-10-27 with `flux-haley-en`:

- `client.speak.v2.connect(*, model, encoding, sample_rate, speed, expressivity, mip_opt_out, tag, authorization, request_options)`, a `@contextmanager` over `wss://api.deepgram.com/v2/speak`. It yields a `V2SocketClient` with `send_speak(SpeakV2Speak)`, `send_flush()`, `send_interrupt()`, `send_configure()`, `send_close()` and a blocking iterator of `bytes | SpeakV2Connected | SpeakV2SpeechStarted | SpeakV2SpeechMetadata | SpeakV2SpeechInterrupted | SpeakV2Flushed | SpeakV2SessionMetadata | SpeakV2ConfigureSuccess | SpeakV2ConfigureFailure | SpeakV2Warning | SpeakV2Error`.
- Per turn: `SpeechStarted`, `Flushed` (≈0.1 s after the `Flush`, before any audio), first audio ≈0.1–0.2 s after the `Flush`, `SpeechMetadata` after the last audio. Turns queue; three `Flush`es sent at once gave three turns in order. Audio starts before any `Flush` once a sentence is complete (words streamed at 100 ms gave first audio after ~1.5 s). `Close` alone flushes the pending text. Generation runs at about 1.4× real time.
- A 900-character turn completed (57 s of audio); 30 one-sentence turns flushed back to back drew no `Warning`. A whitespace-only turn → `Warning NO_SYNTHESIZABLE_TEXT` and a zero-length `SpeechMetadata`; a bare `Flush` with no active turn → `Warning NO_ACTIVE_SPEECH` and no `SpeechMetadata` (the module never sends one: a segment holds at least one `Speak`).
- Through `TTSEngine` (module code, a sink feeding instantly): a 1534-character `say` crossed a segment boundary and gave 164 s of audio in 115 s with no error; first audio 0.75–0.9 s; a `say` cancelled mid-audio surfaced in 0.055 s.
- Handshake: `speed` 0.5 and `expressivity` ±2 accepted; `speed` 0.45, 0.53 or 1.6, `expressivity` 3, an Aura model, → HTTP 400; an unknown Flux voice → HTTP 403; `flux-haley-en` on v1 → HTTP 400. `sample_rate` 8000 and 44100 accepted.

Held socket, both APIs, probed on 2026-10-28 from the development Mac with scratch scripts on the SDK directly (`aura-2-thalia-en`, `flux-haley-en`, 24 kHz, short texts):

- **Sequential utterances on one socket.** `Speak` + `Flush` per utterance with no `Close` works on both, 15 utterances per socket. Each utterance's audio lies between its first `Speak` and its finished-segment answer (`Flushed` on v1, `SpeechMetadata` on v2), and nothing arrived after that answer (0.3 s watched). v1 numbers its `Flushed` `sequence_id` 0, 1, 2, … per socket. On v2 each utterance is a new turn with its own `speech_id`, and its `SpeechMetadata.audio_duration_ms` matched the audio bytes received exactly.
- **Time to first audio** (from the first `Speak`), n = 15 each. On a held socket: v1 mean 186 ms (158–357), v2 208 ms (205–217). On a fresh socket, handshake plus first audio: v1 636 ms (435–682), v2 658 ms (543–695). First audio after the `Speak` is the same on either kind of socket (v1 198 ms, v2 214 ms fresh), so the whole difference is the handshake: 338–455 ms that day, against 285 ms (275–297) measured earlier the same day with 12 opens per API. Holding the socket saves 285–450 ms on every utterance, about 70 % of a fresh utterance's time to first audio at the SDK level.
- **Idle.** Each socket spoke one utterance, sat idle, then spoke again. v1 was alive after 15, 60, 180 and 600 s, both with `websockets`' default 20 s pings and with pings turned off. v2 with pings was alive after 15, 60, 180 and 600 s. v2 with pings off was closed at 58.5–59 s: `Error NET-0004` "The client did not send a websocket message within the required interval", then `ConnectionClosedError` `1011 NET-0004`. Idle beyond 600 s was not measured, and does not matter: `_IDLE_REPLACE` (540 s) replaces any socket idle longer before it is used. An application `{"type": "KeepAlive"}` closed the socket within 0.1 s: `1008 DATA-0000` on v1, `Error MESSAGE-0000` then `1003` on v2. Deepgram documents the Flux limits: a 60 s inactivity window that a Ping or Pong resets, and a one-hour session cap (`NET-0003`). For v1 it documents `NET-0003` "time limit exceeded" with no duration.
- **Cancel, v1.** `Clear` 0.5 s into a long text's audio: audio for up to 0.09 s more, then `Cleared` at 0.10 s, then nothing. This held with the text flushed and unflushed, and with `Clear` sent right after the first `Speak` (no audio at all, `Cleared` at 0.10 s). The `Flushed` of the cancelled `Flush` never came. The next utterance on the same socket: first audio 165 ms, its audio and `Flushed` clean. Sent without waiting for `Cleared`, it still worked, with the stale audio arriving before `Cleared` and the new audio after it. Text pending without a sentence end before `Clear` did not leak into the next utterance (a one-word utterance stayed 0.5–0.7 s long, the same as without it). Two `Clear`s in a row gave two `Cleared`s. A `Clear` with nothing pending → `Cleared`.
- **Cancel, v2.** `Interrupt` 0.5 s into a long turn: audio for up to 0.12 s more, then `SpeechInterrupted` (`audio_played_ms`) at 0.16–0.18 s, with no `SpeechMetadata` for the turn. This held with the turn flushed or not, with `Interrupt` right after the first `Speak` (`SpeechInterrupted` at 0.11 s, no audio), and with no active turn (`SpeechInterrupted` at 0.14 s). The next turn on the same socket started 236–269 ms after its `Speak`, its `SpeechMetadata` duration equal to the bytes received. Unflushed text did not leak into it. A second `Interrupt` while the first was being processed → `Warning INTERRUPT_IN_PROGRESS`, then one `SpeechInterrupted`.
- **Abort by closing (the fallback) costs** a new handshake plus first audio: 656–665 ms on v1, against 165 ms after `Clear` on the held socket. The old socket's close took 0.14–0.15 s.
- **Flush rate, v1** (the documented "20 times every 60 seconds"). Sequential one-word utterances on one socket: 20 succeed in 6 s, and the 21st `Flush` gets `Warning EXCESSIVE_FLUSH` "Rate limit exceeded for flushes", with no `Flushed`. Every `Flush` is refused until 60 s after the first, then accepted again; at one `Flush` per 2.3 s, 20 succeed and the 21st is refused. A new socket opened while the old one was limited accepts its first `Flush`: the limit is per socket. Ten `Clear`s did not count, and a `Clear` did not lift the limit. After a refused final `Flush`, the text (ending a sentence) was spoken anyway, and a `Close` brought the rest of the audio and a clean server close 0.15 s later, with no `Flushed`.
- **Flush rate, v2.** 40 sequential one-word utterances in 31.5 s and then 25 flushes at once drew no warning.
- **Character rate, v1** (`aura-asteria-en`, 212-character utterances back to back on one socket, each waiting for `Flushed`). Without pauses, `1008 DATA-0001` came after 2544 characters in 3.9 s. With a 2 s pause between utterances (73 characters a second), it came after 2544 characters in 32 s. With a 5 s pause (36 characters a second), 60 utterances (12 720 characters, 352 s) passed. Aura-2 at its own pace (about 37 characters a second, generation-bound) also passed 60 utterances. A fresh socket opened right after each close was accepted at once.
- **Through the module** (2026-10-28, after the implementation, `DeepgramModule` directly). The socket was the same one before and after a cancel. A `say` cancelled 0.5 s into its audio surfaced in 19–32 ms. The next `say` had first audio after 406 ms (v1) / 370 ms (v2); that wait includes the cancel's answer. Two more `say`s on the same socket had first audio in 205–210 ms (v1) / 162–164 ms (v2). Construction took 0.38–0.68 s, the handshake included.
- **Latency benchmark** (result `20261027-1453_mac-m5-pro` under `benchmarks/latency/results/`, n = 20 per cell, an instant sink). The table gives mean TTFA in the cells say/short, say/medium, stream/short and stream/medium, against 494–570 ms (Aura-2) and 577–644 ms (Flux) with a socket per utterance on 2026-10-27 (an earlier measurement, superseded in that result):

  | entry | say/short | say/medium | stream/short | stream/medium | `load s` |
  |---|---|---|---|---|---|
  | `aura-2-thalia-en` | 242 ms | 296 ms | 258 ms | 287 ms | 0.61 s |
  | `flux-haley-en` | 184 ms | 185 ms | 176 ms | 188 ms | 0.48 s |
  | `aura-asteria-en` | 234 ms | 233 ms | 169 ms | 225 ms | 0.71 s |

  The `load s` column now includes the handshake. In the benchmark passes made while the held socket was built (2026-10-28, not kept in a result), `aura-asteria-en` lost 4 of 96 runs to `DATA-0001` before the character budget, and none after it. Over those passes, 2 of 192 syntheses failed with `1011 NET-0000`, a Deepgram "internal server error". An 80-utterance held-socket probe did not reproduce it, so it is read as a sporadic server error. It fails that utterance, and the next one reconnects.

## Voice listing

`list_voices(config)` returns the `tts` entries of `GET /v1/models` (`DeepgramClient(api_key).manage.v1.models.list()`). It reads only `api_key` / `api_key_env` and `timeout`, and opens no socket.

- **Selector:** Deepgram has no separate voice parameter, so each entry selects its voice with `{"model": <canonical_name>}`.
- **Metadata:** the gender is the `masculine` / `feminine` word among the metadata tags; the other tags, the accent and the use cases become tags; the age is given as is (`Young Adult`, `Adult`, `Mature`).
- **Languages:** the model's `languages`.
- **Coverage:** entries without a canonical name are skipped. The endpoint lists the Aura voices and the Flux models it publishes there.
- **Errors:** a failure is a `TTSError`.

## Open questions

Deferrals only (design is settled):

1. **Flux `Interrupt` with a `playback_offset`, and `Configure`.** The module uses `Interrupt` with no offset, to cancel on the held socket. An offset would let `SpeechInterrupted` report the text actually heard (`text_spoken`); offsets are cumulative per session, which the held socket would have to track. `speed`/`expressivity` stay fixed per module, so `Configure` is not used. `tag` (v2 only) is not exposed. Decided 2026-10-27: Flux runs inside this module, chosen by the model prefix, not as a `version` field or a module of its own (see "Two APIs").
2. **`base_url` for self-hosted Deepgram.** The client takes a `DeepgramClientEnvironment(base=..., production=wss://..., ...)`, not a single URL. A config field would build one. Not needed for the hosted API.
3. **Connection reuse.** Decided 2026-10-28: the socket is held for the module's lifetime (see "One socket, held for the module's lifetime"). The handshake was 285–450 ms, about half of a fresh socket's 0.49–0.66 s time to first audio, and a held socket's first audio is 186 ms (v1) / 208 ms (v2) after the first text. Cancellation uses `Clear` / `Interrupt`, which the live service answers within 0.2 s with no stale audio after the answer. The idle and rate limits a held socket meets (Flux's 60 s inactivity window, its one-hour cap, v1's 20 flushes per minute) are handled by the default pings, rotation and the flush budget. An unmeasured cutoff past 600 s idle and a half-open socket after host sleep are handled by the idle and slept-host replacement.
4. **Segment thresholds.** 600/900 characters keep at most ~1800 characters pending, under the limit measured live on v1. They are shared with Flux, where no such limit was hit; longer Flux turns would mean fewer turn boundaries but a looser audio bound. Measure before tuning per API. If Deepgram documents the limit differently, the two constants move. No config field until there is a reason.
5. **Default model.** Decided 2026-10-28: `aura-2-thalia-en` stays the default, although Deepgram's own CLI defaults to `flux-alexis-en` since 0.3.0. On the engine's time to first audio (2026-10-27, n=20 per cell, development Mac) Flux (`flux-haley-en`) was slower than Aura-2 in all four cells, by 60–120 ms on the mean (577–644 ms against 494–570 ms), and it generates at about 1.5× real time against Aura-2's ~3×, which leaves less margin over playback. Since Flux loses on latency, cost was not compared. Revisit if Deepgram makes Flux faster or sunsets v1. With the socket held, the handshake no longer counts, and the bare first-audio figures above (186 against 208 ms) keep the same order. The committed benchmark result (`20261027-1453_mac-m5-pro`, "Latency benchmark" above) does not: there Flux reaches first audio first in every cell (184 against 242 ms on `say` short) while still generating slower (1.5× against 2.2× real time), so this choice is worth a second look, as `benchmarks/latency/RESULTS.md` notes.
6. **A tighter audio bound.** The unread audio is bounded by the lead plus one segment ("Bounded unread audio"). Shorter segments would tighten it at the cost of more flushes. On v1 every flush is drawn from the 20-per-minute budget the held socket now shares across utterances, so shorter segments would also mean more rotations.
