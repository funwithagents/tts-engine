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

`deepgram` implements `TTSModule` using Deepgram's hosted **Aura-2** voices through the official `deepgram-sdk` Python package and its speak v1 WebSocket. It asks for `linear16` output, which is signed 16-bit mono PCM at the module's declared rate, so audio goes from the socket to `callback` as is: no codec and no float conversion. It is one API-backed provider among others, not the default and not part of the base install, and ships behind the `deepgram` packaging extra (see "Dependencies").

Its shape is the ElevenLabs one: a **synchronous** SDK client driven from the cancellable worker, with no private event loop. It is a **native text-stream** backend. The socket takes repeated `Speak` messages and starts speaking before the text is complete, so `synthesize_text_stream` forwards text as it arrives and does no sentence grouping. A complete text goes over the same socket in small slices (`slice_text`), so the same admission gate applies to both shapes.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "deepgram"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Deepgram API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Deepgram's own convention is `DEEPGRAM_API_KEY`). Lets a config file be committed with no secret. |
| `model` | non-empty string | no | `"aura-2-thalia-en"` | The Aura model, which **is** the voice: `aura-2-<voice>-<language>` (e.g. `aura-2-hector-fr`, `aura-2-julius-de`), or a first-generation `aura-<voice>-en`. Deepgram has no separate voice parameter. |
| `sample_rate` | integer | no | `24000` | Output PCM rate. One of `8000`, `16000`, `24000`, `32000`, `48000`, the `linear16` rates the SDK documents. |
| `speed` | number | no | — (API default 1.0) | Speaking-rate multiplier, `0.7`–`1.5` (the API rejects values outside this range at the handshake). |
| `mip_opt_out` | boolean | no | — (API default: opted in) | Opt the requests out of Deepgram's Model Improvement Program, which affects pricing. |
| `timeout` | finite number > 0 (seconds) | no | `30` | Bound on the socket's handshake (the SDK exposes no open timeout of its own, so the module polls the connect against it) and on a wait for the server: a `Flush` unanswered with no message at all for this long is a stalled request. Either fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

### API key resolution

Same rules as ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. A non-string value raises `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` naming the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `DeepgramClient(api_key=...)`. The module never relies on the SDK's own `DEEPGRAM_API_KEY` fallback.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `model`: a non-empty string (defaulted when absent).
- `sample_rate`: if present, an integer in `{8000, 16000, 24000, 32000, 48000}`. Booleans are rejected rather than treated as integers.
- `speed`: if present, a number (not a bool) in `[0.7, 1.5]`.
- `mip_opt_out`: if present, a boolean.
- `timeout`: if present, a finite number (not a bool) `> 0`.

`speed` and `mip_opt_out` are **left out of the connection when unset**, so the API's own defaults apply. Whether a `model` exists is only known to the API: an unknown one fails at the first synthesis as `TTSError` (the handshake answers HTTP 403).

## Implementation notes

### SDK entry point

```python
from deepgram import DeepgramClient
from deepgram.speak.v1.types import SpeakV1Text

client = DeepgramClient(api_key=key)
with client.speak.v1.connect(
    model=self._model,
    encoding="linear16",
    sample_rate=self._sample_rate,
    # speed=..., mip_opt_out=... only when set
) as socket:  # V1SocketClient over a websockets.sync connection
    socket.send_text(SpeakV1Text(text="Hello "))
    socket.send_flush()
    socket.send_close()
    for message in socket:  # bytes (audio) or a typed event
        ...
```

One client is built in `__init__` and reused. Each utterance opens **one socket** (`speak.v1.connect`, a context manager whose exit closes the WebSocket), entered through `open_cancellable` so the handshake honors `stop` and `timeout` (see "Cancellation"). The engine's `say` lock means at most one is open at a time, apart from the closing tail of an aborted one (see "Cancellation").

Messages from the server are either `bytes` (audio) or typed events, and the module dispatches on the event's `type` string, not its class. The SDK parses `Cleared` into the `SpeakV1Flushed` class, so `isinstance` would mislabel it.

| `type` | Meaning | Module action |
|---|---|---|
| (bytes) | PCM audio | fed to `callback` |
| `Metadata` | request id, model | ignored |
| `Flushed` | every text sent before the matching `Flush` has been spoken | counted (see "Pending-text backpressure") |
| `Warning` | e.g. `EXCESSIVE_FLUSH` | logged at `WARNING` |
| `Cleared` | answer to `Clear` | ignored (the module never sends `Clear`) |

### Output format and sample rate

`encoding="linear16"` is headerless signed 16-bit little-endian mono PCM at exactly `sample_rate` (confirmed live: 1920-byte messages at 24 kHz, i.e. 40 ms frames). That matches the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with **no conversion in the module**. The worker feeds the messages through the shared `PCMFeeder` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers"): an odd trailing byte is carried over to the next message, so `callback` only ever gets whole samples (the API has not been seen to split a sample), and a message is handed over in slices of at most 100 ms, `stop` polled between them. `sample_rate` returns the configured value and is fixed for the module's lifetime.

### Threads: sender, reader, worker

The SDK socket is synchronous, and its `recv` and iterator have **no timeout**. A worker blocked in `recv` while the server is silent (an LLM pausing mid-answer) could not see `stop`. The module therefore splits one utterance over three threads that share the socket (websockets' sync connection allows sends concurrent with receives):

- **Worker** (from `run_cancellable_worker` / `run_text_stream_worker`): opens the socket through `open_cancellable`, starts the other two, then takes messages from a `queue.Queue` with a **100 ms timeout**. It checks `stop`, the sender's error and the stall rule on every wake and calls `callback` for audio, subtracting what it feeds from the unread count. It is the only thread that calls `callback`, so calls never overlap.
- **Reader** (daemon): iterates the SDK socket and puts each message, or the exception that ended the iteration, or an end marker, on the queue. It also counts the audio bytes it has read (`unread`), notes when the server last spoke, and counts `Flushed` answers itself, so the sender's gates (below) open even while the worker is busy feeding audio.
- **Sender** (daemon): forwards text, then the final `Flush` and `Close` (see the next sections).

`synthesize_text(text)` runs this same machinery with `slice_text(text, 50)` as the piece source: the complete text goes in verbatim slices of at most 50 characters (cut at a sentence end, else at whitespace), so the admission gate has a grain. The two input shapes differ only in where the pieces come from.

### Text stream (native)

`synthesize_text_stream` is a **true stream-to-stream** pass. The live API starts producing audio before any `Flush`: words streamed one per message with 100 ms gaps gave first audio after 0.3 s, and only a trailing fragment with no sentence end waited for the `Flush`. The sender:

1. pulls the bridge's `pieces` and groups them at **word boundaries** with `split_at_last_space`, so a sub-word LLM token never reaches the server alone; each group is one `Speak` message;
2. cuts the text into **segments**, each ending with a `Flush` (next section);
3. at end of stream, sends the held-back remainder, a final `Flush` if anything is pending, then `Close`. An empty stream sends only `Close`.

If `stop` ends `pieces` early, the sender sends nothing more: no final `Flush`, no `Close`.

### Pending-text backpressure

The server rejects an utterance once about **2000 characters** of text are waiting to be spoken. Live, it either closes the socket with `1008 DATA-0001` after about 2000 characters of audio, or (with one word per message) stops answering until the keepalive times out. A fast LLM easily outruns synthesis (which runs at about 2× real time), so long answers would fail without flow control. `Flush` is the only acknowledgment the protocol offers, and it is **rate-limited**: past about 20 quick flushes, the server answers `Warning EXCESSIVE_FLUSH` and ignores them. A `Flush` per sentence is therefore not an option.

The module bounds the pending text with **flushed segments, one at a time at the server**:

- A segment is flushed at the first sentence end once it holds **600** characters, or at a word boundary at **900** characters at the latest. Text longer than the room left in a segment is cut at its last sentence end within that room, else at its last whitespace, else hard at the limit (a single "word" longer than a segment) — `cut_at_boundary`.
- The first text of a new segment is sent only once the previous segment's `Flush` has been answered by `Flushed`, i.e. once the server has **generated** all of it. The sender polls `stop` every 100 ms while it waits.

The server therefore never holds more than one segment (≤ 900 characters) of unspoken text, and the audio it has generated ahead of playback is bounded by that segment (see the next section). With segments of at least 600 characters (about 40 s of speech), the flush rate stays far under the limit. Playback is not interrupted: generation runs at 2× real time and the module keeps a 10 s lead, so `Flushed` for segment *n* arrives while segment *n* is still playing and segment *n+1* is admitted long before the audio runs out. (The first version allowed one unanswered `Flush`, i.e. two segments at the server; validated live then: a 2925-character stream sent one word at a time in about 1 s gave 170 s of continuous audio with no error, where the same stream without the gate failed with `DATA-0001`.)

A complete text goes through the same segmentation, so `say` with a text longer than 2000 characters also works.

### Bounded unread audio

The server generates at about 2× real time while the default sink feeds at 1×, so without a second gate the audio read and not yet fed would grow with the utterance's length (about 48 kB per second of audio ahead at 24 kHz). The sender therefore **admits** a `Speak` message only while the unread audio is under **10 s** at the configured rate (`_MAX_UNREAD_SECONDS`): the reader counts bytes in, the worker counts bytes out, and the sender waits on the shared condition, polling `stop`, until playback has caught up. Text admission is thereby coupled to playback progress, which also back-pressures the text bridge and, through it, the LLM.

What this bounds: the unread audio never exceeds the lead plus the audio of the text already admitted, and the latter is at most one segment (previous section) — in the order of a minute for 900 characters, not of the utterance. What it does not change: the initial lead is built as fast as the server generates, so first audio is as prompt as before; the rule is the shared one in [tts-module-interface.md](../tts-module-interface.md), "Bounded unread audio". Measured values are under "Validated against".

### End of utterance

The final `Flush` is followed by `Close`. The server then finishes the remaining audio, answers `Flushed` and closes the socket. The worker stops as soon as `Close` has been sent, every `Flush` has been answered, and the queue is empty. A server close (the reader's end marker) after `Close` also ends the utterance normally, and so does a connection error once the last `Flushed` has arrived (observed once, as a close with no close frame). A server close **before** the module sent `Close` means audio was lost, and raises `TTSError`.

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"). The worker sees `stop` within one 100 ms poll (sooner when audio is flowing), stops calling `callback` and leaves its loop. It then sets `stop` itself, which releases a sender waiting on text, on a `Flushed` or on the admission gate, and the coroutine re-raises `CancelledError` once the worker thread has exited.

**During the handshake.** `speak.v1.connect` blocks in `websockets`' connect and the SDK exposes no open timeout, so the worker enters it through `open_cancellable`: the connect runs on a helper thread while the worker polls `stop` every 100 ms against `timeout`. A cancel during the handshake surfaces within one poll with nothing sent; the connection, if it completes afterwards, is closed on the helper thread. A handshake slower than `timeout` is `TTSError("not open within …")`.

On an **aborted** utterance (cancelled, or failed), the socket is closed on a separate daemon thread rather than on the worker. Live, the server answers the WebSocket close handshake only once it has finished generating the whole text, which took 4–9 s for a long text. The worker would otherwise block that long before `say` could return, while Gradium and Mistral cancel in about 0.05 s. A `Clear` before closing did not shorten the handshake. Callbacks have already stopped, so the late close is harmless: the socket just lingers until the server lets go, and the reader keeps draining it meanwhile, so the handshake is never starved by unread audio. A normally completed utterance closes on the worker, which is immediate because the server is done. Measured on the live tier: `say` and `say_stream` cancels surface in about 0.05 s.

### Dependencies and lazy import

Requires the `deepgram` extra: `pip install tts-engine[deepgram]` / `uv sync --extra deepgram`, declared in `[project.optional-dependencies]` as `deepgram = ["deepgram-sdk>=7.15,<8"]` and added to the `all` aggregate. The `<8` pin is there because the SDK is Fern-generated and the module relies on the 7.x `speak.v1` socket client. The SDK is pure Python (`httpx`, `pydantic`, `websockets`), so it is a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `deepgram/module.py` must **not** import `deepgram` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    from deepgram import DeepgramClient
    from deepgram.speak.v1.types import SpeakV1Text
except ImportError as exc:
    raise ConfigError(
        "The 'deepgram' module requires the deepgram extra: pip install tts-engine[deepgram]"
    ) from exc
```

`tests/modules/test_lazy_imports.py` adds `deepgram` to the poisoned modules and the `deepgram` row to its construction loop. Importing the SDK triggers two `websockets` deprecation warnings (its fallback imports of the legacy client and `InvalidStatusCode`). They are upstream and harmless, and are filtered in `pyproject.toml`'s pytest `filterwarnings`, scoped to those messages.

### Error handling

- Any failure of the SDK or the socket is re-raised as `TTSError(f"Deepgram request failed: {exc}")`, chained via `raise ... from exc`. That covers a rejected handshake (HTTP 401 bad key, 403 unknown model, 400 bad parameter, raised by `websockets` as `InvalidStatus`), a server close with an error code (`1008 DATA-0001`), a keepalive timeout, and a failed send on the sender thread. A server close before `Close` is a `TTSError` of its own.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as Deepgram request failures, because `callback` is called outside the provider `try`.
- A text-stream failure (the async source raising) aborts the utterance and propagates unchanged through `run_text_stream_worker`, and no `Close` is sent.
- A **stall** — a `Flush` sent, not answered, and no message of any kind from the server for `timeout` seconds — is `TTSError("Deepgram request failed: no answer within … s")`. A pause in the text stream is not a stall: nothing is pending then. A handshake slower than `timeout` is a `TTSError` too (see "Cancellation").
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.
- `Warning` events are logged, not raised. The gate keeps the module clear of `EXCESSIVE_FLUSH`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` releases nothing of its own: `DeepgramClient` exposes no close and holds only a lazily opened HTTP pool, which goes with the module; every socket is per utterance.

## Module ID

Registered in `REGISTRY` as `"deepgram"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_deepgram.py`)**: `deepgram` and `deepgram.speak.v1.types` are **faked** in `sys.modules`. The fake `DeepgramClient` has a `speak.v1.connect` that records its kwargs and returns a context manager over a fake socket. That socket records what is sent and answers like the live API: audio per `Speak`, `Flushed` per `Flush` (or held back until released), end of stream after `Close`. It can also run a fixed script of messages and exceptions. The tests cover: A float `sample_rate` (`24000.0`) is among the rejected values.
  - config: `ConfigError` on a missing extra, on a missing or empty key, and on each invalid field (parametrized, `timeout` included); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the connect kwargs (defaults, and `speed`/`mip_opt_out` only when set);
  - complete text: the order text, `Flush`, `Close`; audio reaching the callback unchanged, with `Metadata` skipped and `Warning` logged; the odd-byte carry-over; a long text sent in slices of at most 50 characters that concatenate back to it, cut into flushed segments of at most 900 characters, each ending at a sentence end; an over-long word hard-cut; the sender sending nothing of the next segment until `Flushed` arrives;
  - connection setup: a cancel during a held handshake surfacing within a second, the late socket closed with nothing sent; a handshake slower than `timeout` as `TTSError`;
  - bounded unread audio: with the bound shrunk to one chunk and the callback blocked, the sender stops short of the whole text, then sends the rest once playback resumes; a `Flush` left unanswered by a silent server as `TTSError` after `timeout`;
  - errors: connect, mid-stream and send failures wrapped in `TTSError`; a server close before `Close` as `TTSError`; callback errors propagating unchanged with the socket closed; cancellation mid-audio stopping callbacks before `CancelledError`, with the socket then closed;
  - text stream: word groups, then `Flush` and `Close`; an empty stream sending only `Close`; audio reaching the callback before the source has yielded its last piece; a source error propagating unchanged with no `Close`; cancellation while the source is open and the server silent, surfacing promptly.
- **Live (`tests-e2e/test_modules.py`)**: one `MODULES` row (`deepgram`, `api_key_env: "DEEPGRAM_API_KEY"`, `aura-2-thalia-en`) plus `_REQUIRED_IMPORT["deepgram"] = "deepgram"` in `support.py`. Every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is missing ([testing.md](../testing.md), "Live tier"). CI maps an optional `DEEPGRAM_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `deepgram-sdk==7.15.0`

Confirmed from the package and from live calls on 2026-10-26:

- `from deepgram import DeepgramClient`; `DeepgramClient(api_key=...)`. Without `api_key` it reads `DEEPGRAM_API_KEY` (not relied on). `client.speak.v1.connect(*, encoding, mip_opt_out, model, sample_rate, speed, authorization, request_options)` is a `@contextmanager` over `websockets.sync.client.connect` to `wss://api.deepgram.com/v1/speak`, yielding a `V1SocketClient` with `send_text(SpeakV1Text)`, `send_flush()`, `send_clear()`, `send_close()`, `recv()` (no timeout) and a blocking iterator of `bytes | SpeakV1Metadata | SpeakV1Flushed | SpeakV1Cleared | SpeakV1Warning`. `speak.v1.types.SpeakV1Text(text=...)` has `type="Speak"`.
- `linear16` at 24 kHz arrives as 1920-byte messages. First audio comes 0.13–0.3 s after the first text, and synthesis runs at about 2× real time.
- Audio starts **before** `Flush`; only text after the last sentence end waits for it. `Flush` → `Flushed` (with a `sequence_id`); `Clear` → `Cleared` within about 0.1 s; `Flush` + `Close` → remaining audio, `Flushed`, then a clean server close.
- More than about 2000 characters pending → `1008 DATA-0001` close (or, with one word per message, a stalled server). More than about 20 quick flushes → `Warning EXCESSIVE_FLUSH`.
- Handshake rejections: bad key → HTTP 401, unknown model → HTTP 403, `speed` 0.5 or 2.0 → HTTP 400, `sample_rate` 22050 → HTTP 400 (raised as `websockets.exceptions.InvalidStatus`). `speed` 0.7 and 1.5 are accepted. `sample_rate` 44100 is also accepted, but the module sticks to the documented set.
- The client's close handshake on a socket still generating blocks until generation ends (8.6 s measured for an 8-sentence text cancelled after 1.5 s).
- Bounded unread audio (2026-10-27, the module through `TTSEngine` with a sink feeding in real time, the unread count sampled 20× a second): a 200-sentence `say` peaked at 32.1 s of audio over a 75 s window and 32.5 s over 130 s — a plateau at the lead plus one segment, where the server at 2× real time would otherwise have been a minute ahead; with the earlier two-segment gate the same probe peaked at 54.6 s over 40 s. The client side of the socket is quiet for up to a minute at a time while the gate holds text, with no idle close from the server. First audio 0.5–0.7 s, unchanged by the gate.

## Open questions

Deferrals only (design is settled):

1. **Flux TTS (`speak.v2`)**. Deepgram's newer turn-based model (`client.speak.v2.connect(model="flux-*")`, early access) adds `Interrupt`, `expressivity` and per-turn metadata. It is a different socket protocol with its own model ids. It could be a `version: 2` switch in this module or a module of its own; decide once it is GA and the key has access.
2. **`base_url` for self-hosted Deepgram.** The client takes a `DeepgramClientEnvironment(base=..., production=wss://..., ...)`, not a single URL. A config field would build one. Not needed for the hosted API.
3. **Connection reuse.** One socket per utterance is the simplest correct shape, as for the other providers. Keeping one open across utterances (`Flush` between them) would save the handshake (~0.1 s), but it would need `Clear` for cancellation and care with the socket's idle timeout. Measure before adding it.
4. **Segment thresholds.** 600/900 characters keep at most ~1800 characters pending, under the limit measured live. If Deepgram documents the limit differently, the two constants move. No config field until there is a reason.
5. **A tighter audio bound.** The unread audio is bounded by the lead plus one segment ("Bounded unread audio"). Shorter segments would tighten it at the cost of more flushes, against a rate limit that is only known as "about 20 quick ones". Measure the limit before moving the constants.
