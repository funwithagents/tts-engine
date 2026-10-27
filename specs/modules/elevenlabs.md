---
code:
  - src/tts_engine/modules/elevenlabs/__init__.py
  - src/tts_engine/modules/elevenlabs/module.py
tests:
  - tests/modules/test_elevenlabs.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# ElevenLabs Module

**Status:** Implemented

## Overview

`elevenlabs` implements `TTSModule` using the ElevenLabs streaming APIs: the text-to-speech HTTP endpoint for a complete text (every model), and for a text stream one of two stream-input WebSockets, picked by the model (see "Two sockets"). It requests MP3 output and incrementally decodes the encoded stream to raw signed 16-bit PCM mono in-process before the callback, so `AudioPlayer` always receives PCM. It is one API-backed provider among others — not the default and not the base install — and ships behind the `elevenlabs` packaging extra (see "Dependencies").

## Two sockets

ElevenLabs serves its models on two real-time sockets, and each rejects the other's models at the WebSocket handshake:

- the **text-to-speech** stream-input socket (`/v1/text-to-speech/{voice_id}/stream-input`) serves the v2 family — `eleven_flash_v2_5`, `eleven_flash_v2`, `eleven_multilingual_v2`, the deprecated `eleven_turbo_v2*` — and answers HTTP 400 to a v3/v4 model ("No `eleven_v3` or `eleven_v4` on this endpoint");
- the **text-to-dialogue** stream-input socket (`/v1/text-to-dialogue/stream-input`) serves the v3/v4 models — `eleven_v3`, `eleven_v3_conversational`, `eleven_v4`, `eleven_v4_turbo` (the flagship real-time model, ~100 ms median) — and answers HTTP 403 to a v2 model. Its documented rule is that `model_id` "must start with `eleven_v3` or `eleven_v4`".

The module therefore needs no switch of its own: a `model` starting with **`eleven_v3` or `eleven_v4`** streams over the dialogue socket, anything else over the TTS socket. Both are supported because both are current: the v2 family is ElevenLabs' documented low-latency recommendation (`eleven_flash_v2_5`, ~75 ms) and its own price line, and the TTS socket has no sunset announced. Following [project.md](../project.md) ("Dependency strategy for TTS backends"), the TTS-socket branch goes when ElevenLabs sunsets it. The default stays `eleven_flash_v2_5`.

The **complete-text path does not branch**: the HTTP `text_to_speech` endpoint accepts every model, v3/v4 included (the models page documents v3 on it, and the endpoint's own reference names v4 voice settings), in one request of up to 5,000–40,000 characters depending on the model. The text-to-dialogue HTTP endpoint also serves v3/v4 but caps a request at about 2,000 characters, which would force `say()` to split a long text; it is not used.

Two sockets, one loop (see "Text stream"): the sender/receiver loop is the same for both, and a small protocol record (`_SocketProtocol`, built once at construction) carries what differs — the URL, the opening frame, the text frame, the end-of-stream frame, the server's final flag, and whether an idle keep-alive is needed:

| | TTS socket (v2 family) | Dialogue socket (v3/v4) |
|---|---|---|
| URL | `…/v1/text-to-speech/{voice_id}/stream-input?model_id=…&output_format=mp3_44100_128` | `…/v1/text-to-dialogue/stream-input?model_id=…&output_format=mp3_44100_128` |
| Opening frame | `{"text": " ", "voice_settings": {stability, similarity_boost}, "generation_config": {"chunk_length_schedule": [50]}}` | `{"voices": [voice_id], "settings": {"stability", "similarity"}}` |
| Text frame | `{"text": words, "try_trigger_generation": true}` | `{"inputs": [{"text": words, "voice_id": voice_id}]}` |
| End of stream | `{"text": ""}` | `{"close_socket": true}` (generates what is buffered, then the final frame) |
| Final flag | `isFinal` | `is_final` |
| Server buffering | from `chunk_length_schedule` (50 characters, the minimum) | fixed: about 40 characters and 8 words, then audio flows as text arrives |
| Inactivity | generous, configurable | fixed 20 s between client frames: the receiver sends `{"keep_alive": true}` after 10 s of silence |
| Concurrency | generation time counts against the plan's concurrency | each open socket holds one *dialogue session* from a separate, smaller pool |

Authentication is the `xi-api-key` header on both. Both sockets report a rejected request as a text frame carrying `error`, and both may close without a close frame on a bad voice; both cases are `TTSError` ("Error handling"). The dialogue socket also sends informational frames with neither `audio` nor the final flag (`is_final_audio_for_turn`); the receiver ignores them. `stability` and `similarity_boost` are sent to both sockets (the dialogue socket accepts the `settings` frame; whether it honors it is not documented) and to the HTTP endpoint.

The probe record behind this section (2026-10-27, scratch scripts): the HTTP TTS endpoint answered 200 with audio to `eleven_flash_v2_5`, `eleven_v3`, `eleven_v3_conversational`, `eleven_v4` and `eleven_v4_turbo`; the TTS socket answered HTTP 400 to `eleven_v3` and `eleven_v4_turbo`; the dialogue socket answered audio and `is_final` to the four v3/v4 models and HTTP 403 to `eleven_flash_v2_5`; the dialogue HTTP endpoint answered `model_does_not_support_dialogue` to the two v2 models. On the dialogue socket, audio for four 20–40-character groups sent 1 s apart started 1.1 s in (after the second group) and kept flowing before `close_socket`; two `keep_alive` frames 10 s apart carried a 25 s pause; `close_socket` with no text answered `is_final` at once; an invalid voice closed the socket with no close frame.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "elevenlabs"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | ElevenLabs API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key. Lets a config file be committed with no secret. |
| `voice_id` | non-empty string | yes | — | The voice ID used for synthesis. |
| `model` | non-empty string | no | `"eleven_flash_v2_5"` | ElevenLabs model ID, which picks the socket for a text stream ("Two sockets"): a v2 model — `eleven_flash_v2_5` (recommended for low latency), `eleven_multilingual_v2` (quality) — streams over the text-to-speech socket; a model starting with `eleven_v3` or `eleven_v4` — `eleven_v4_turbo` (the flagship real-time model), `eleven_v4`, `eleven_v3`, `eleven_v3_conversational` — over the text-to-dialogue socket. A complete text goes over HTTP with every model. |
| `stability` | number | no | `0.5` | Voice stability (0.0–1.0). |
| `similarity_boost` | number | no | `0.75` | Similarity boost (0.0–1.0). |
| `timeout` | finite number > 0 (seconds) | no | `30` | Bound on the HTTPS request of `synthesize_text` — the wait for the response and for each read of its body — passed as the SDK's `request_options.timeout_in_seconds` (whole seconds, rounded up), instead of the SDK's 240 s default; and the open timeout of the stream-input WebSocket. A stalled request fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

### API key resolution

The module calls the shared `resolve_api_key` ([tts-module-interface.md](../tts-module-interface.md), "Shared config helpers"), which every API-backed module uses with its own label; this section is the reference description of the rule. The key is resolved in this order: a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither `api_key` nor `api_key_env` yields a key, construction raises `ConfigError`. Prefer `api_key_env` so `config.json` can be committed without a secret.

`voice_id` and `model` are validated as non-empty strings. `stability` and `similarity_boost` are validated at construction (through the shared `finite_number`) as numeric values in the inclusive range 0–1; booleans are rejected rather than treated as integers, and so are `NaN` and the infinities. Invalid module configuration therefore fails as `ConfigError` before the SDK client is used.

## Implementation notes

### SDK vs raw HTTP

Use the official `elevenlabs` Python SDK. Its `client.text_to_speech.stream(...)` returns a synchronous iterator of `bytes` chunks. Use this streaming interface.

### Output format and decoding

Request `output_format="mp3_44100_128"` from the ElevenLabs API (128 kbps MP3 at 44100 Hz). MP3 chunks are then decoded to raw signed 16-bit PCM mono using `miniaudio.stream_any` before being passed to the callback — so `AudioPlayer` always receives PCM regardless of the upstream format.

The ElevenLabs SDK streaming interface yields `bytes` chunks of MP3 data. `miniaudio.stream_any` requires a `miniaudio.StreamableSource` (a `read(num_bytes)` interface), not a bare generator, so the MP3 chunk iterator is wrapped in a small `StreamableSource` adapter (`_ChunkSource`). `stream_any` decodes to PCM `array.array` chunks; call `callback(pcm_chunk.tobytes())` for each.

The adapter **serves short reads**: `read(num_bytes)` returns whatever is buffered (pulling one provider chunk when the buffer is empty), never blocking until `num_bytes` have arrived, and returns `b""` only at end of stream. This is what keeps the module streaming — the MP3 decoder requests about 64 KB per read, roughly 4 s of audio at 128 kbps, so an adapter that filled each request would hold back the first PCM chunk until that much audio had been downloaded (a 4 s start delay at real-time delivery, hidden only when the API runs far ahead of real time). miniaudio's `StreamableSource` contract allows short reads and the MP3 decoder resumes across them, so the decoded output is identical.

```python
class _ChunkSource(miniaudio.StreamableSource):
    """Wraps a bytes-chunk iterator as a miniaudio StreamableSource."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buf = bytearray()

    def read(self, num_bytes: int) -> bytes:
        # Short reads: serve what is buffered, pull one chunk when empty;
        # b"" only at end of stream. Never block until num_bytes arrive.
        while not self._buf:
            try:
                self._buf.extend(next(self._chunks))
            except StopIteration:
                return b""
        data = bytes(self._buf[:num_bytes])
        del self._buf[:num_bytes]
        return data


async def synthesize_text(self, text, options, callback):
    def _blocking_stream(stop: threading.Event) -> None:
        # Build the provider request + decoder inside the TTSError boundary,
        # then loop: advance the decoder inside that boundary, invoke callback
        # outside it (so playback failures keep their identity), and poll
        # `stop` between chunks for cooperative cancellation.
        ...

    await run_cancellable_worker(_blocking_stream)
```

The ElevenLabs SDK streaming method is synchronous (returns an iterator). The entire decode-and-feed loop is wrapped in `asyncio.to_thread` to avoid blocking the event loop. Cancellation is cooperative via `run_cancellable_worker` (see [tts-module-interface.md](../tts-module-interface.md), "Cancellation"): the worker polls the stop flag between decoded chunks, and the coroutine waits for the thread to finish before propagating `CancelledError`, so no callback can occur after `synthesize_text()` exits.

### Text stream: the stream-input WebSocket (native)

`synthesize_text_stream` is a **true stream-to-stream** pass: it does not group the text into sentences. It drives the model's ElevenLabs *stream-input* WebSocket ("Two sockets"; authenticated with the `xi-api-key` header) with the module's **own loop**, over `websockets.sync`, which the SDK already depends on — not the SDK's `convert_realtime` (why not: below). The loop reads every socket-specific frame from the `_SocketProtocol` record built at construction; the frames below are the TTS socket's, the dialogue socket's are in the table above.

The worker comes from `run_text_stream_worker` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers") and runs two threads over one connection:

- **Sender** (a daemon thread) — pulls the bridge's `pieces`, groups them at **word boundaries** with `split_at_last_space` (so a sub-word LLM token is never sent alone), sends each group as the protocol's text frame (`{"text": …, "try_trigger_generation": true}`), then the remainder and the end-of-stream frame (`{"text": ""}`). If `stop` ends the iterator early, no end-of-stream is sent: the request is dropped.
- **Receiver** (the worker thread, as a generator of MP3 chunks) — opens the socket, sends the opening frame (`text: " "` with the `voice_settings` and `generation_config.chunk_length_schedule = [50]`, the API's minimum), then receives continuously with a **100 ms poll** (`recv(timeout=0.1)`), checking `stop` and the sender's error on each wake, decoding each message's base64 `audio`, until the server's final flag (`isFinal`), `stop`, or a failure on either side. On a protocol with a keep-alive frame (the dialogue socket), a poll that wakes with nothing received and **10 s since the last client frame** sends it — `websockets.sync` guards sends with a mutex, so the receiver can send beside the sender thread — which keeps a paused source (an LLM mid-answer) inside the server's fixed 20 s inactivity limit. A text message carrying `error` (the API's protocol error: `{"message": …, "error": …, "code": …}`, sent before it closes the socket for a rejected request or an exhausted quota) is a `TTSError` naming the message. A **clean close before the final flag** is a `TTSError` too ("closed the stream before its final message"): the request did not complete, so the audio that came is not the whole utterance — it was previously taken for a successful end, which turned a rejected request into a silent, empty success. Frames with neither `audio` nor the final flag are ignored.

The MP3 chunks feed the **same** `_ChunkSource` + `miniaudio.stream_any` decode as `synthesize_text` through one `_decode_and_feed(open_raw, callback, stop)` loop (open the raw source and build the decoder inside the `try/finally` that closes the raw source, so a decoder failure releases it too; advance the decoder inside the `TTSError` boundary, call `callback` outside it, poll `stop` between chunks). The loop peeks the first raw chunk before building the decoder: **no audio means no decoder** and no callback — miniaudio cannot initialize a decoder on empty input, so the decision has to come first. `sample_rate` stays 44100.

An **empty stream** (no non-empty piece) sends no text and no end-of-stream: the sender notes it, the receiver leaves the `with connect(...)` block without waiting for the final flag (dropping the connection), and with no raw audio nothing is decoded or fed — the interface's "an exhausted stream with no text produces no audio and is not an error". The socket is still opened as soon as the worker starts, so the handshake overlaps the wait for the first piece rather than following it.

Error and cancellation rules are unchanged: connection, socket and decoder failures — on either thread — become `TTSError("ElevenLabs request failed: …")`; callback failures propagate unchanged; a text-stream failure aborts via the bridge and propagates unchanged; cancellation stops the receiver within one poll interval, closes the socket (leaving the `with connect(...)` block), and the thread exits before `synthesize_text_stream` raises. A sender still blocked on text is released when `stop` reaches `pieces`; its late send on the closed socket is ignored.

**Why not the SDK's `convert_realtime`.** It pulls text and audio on one thread: for each text piece it sends, it polls the socket once with a 10 ms timeout, and only after the text iterator ends does it drain the rest. When the text source pauses — an LLM mid-answer — it blocks on the next piece and receives **nothing**, so playback stalls for the length of the pause and a cancel cannot be honored until text resumes. Found by the live cancellation scenario in plan `202610261310_cancellation-tests-and-elevenlabs-stream-loop`; with a continuously fed source the two loops measure the same (first audio ~0.7 s into a 60 ms/word stream).

### Dependencies and lazy import

Requires the `elevenlabs` extra: `pip install tts-engine[elevenlabs]` / `uv sync --extra elevenlabs`, declared in `[project.optional-dependencies]` as `elevenlabs = ["elevenlabs>=2.39,<3", "miniaudio"]` — the official SDK plus `miniaudio` for the MP3 → PCM streaming decode. The `<3` bound is the 2.x `text_to_speech.stream` surface the module calls (validated on 2.74.1, which still takes every parameter the module passes); the stream-input WebSocket is driven by hand, so it does not depend on the SDK version. Neither is a base dependency (see [project.md](../project.md), "Dependency strategy for TTS backends").

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `elevenlabs/module.py` must **not** import `elevenlabs` or `miniaudio` at file top. Both imports happen inside `__init__`, turning a missing extra into an actionable `ConfigError` before any config field is read:

```python
try:
    import miniaudio
    from elevenlabs import ElevenLabs
    from elevenlabs.types import VoiceSettings
except ImportError as exc:
    raise ConfigError(
        "The 'elevenlabs' module requires the elevenlabs extra: "
        "pip install tts-engine[elevenlabs]"
    ) from exc
```

Because `_ChunkSource` subclasses `miniaudio.StreamableSource`, that class cannot be defined at module top either. It is built by a small factory, `_make_chunk_source_class(miniaudio_module)`, called once in `__init__`; the constructor stores the imported `miniaudio` module, the `VoiceSettings` class, and the resulting adapter class on the instance for both synthesis methods to use. Nothing observable changes: `sample_rate`, config validation, streaming, and error handling are as specified above.

### Error handling

- Any exception raised by the SDK or decoder (API errors, auth failures, network errors, invalid audio) is caught and re-raised as `TTSError(f"ElevenLabs request failed: {exc}")`, chained from the original via `raise ... from exc`.
- A stream-input protocol error message, and a clean close before the final flag, are `TTSError` (see "Text stream"); already-labeled errors from the raw source are not wrapped a second time. A model the socket does not serve cannot happen, since the prefix picks the socket; an unknown model fails at the first synthesis as `TTSError` (the handshake is refused).
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as ElevenLabs request failures.
- The raw source (the HTTP response generator, or the stream-input socket) is released through the shared `cleanup` helper: a failing release never replaces a synthesis, callback or source error, or a cancellation — it is logged ([tts-module-interface.md](../tts-module-interface.md), "`cleanup`").
- Cancellation requests cooperative worker shutdown and waits until callback activity has stopped before propagating.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` releases nothing of its own: the `ElevenLabs` client exposes no close and holds only a lazily opened HTTP pool, which goes with the module; every socket and decoder is per utterance.

## Module ID

Registered in `REGISTRY` as `"elevenlabs"`.

## Open questions

Deferrals only (design is settled):

1. **PCM output for the stream.** Both stream-input sockets also serve `pcm_*` formats, which would drop the MP3 decode; MP3 is kept so both methods share one decode path and one declared rate.
2. **Dialogue features.** The dialogue socket takes several voices (`eleven_v4`: up to 10; `eleven_v4_turbo`: one), `new_turn`, a mid-stream `flush`, and `audio_effects`. The module uses it as a single-voice stream; those features would be a `TTSOptions` extension, not a change to this module's contract.
3. **`say()` over the dialogue HTTP endpoint.** Not done (the 2,000-character cap, "Two sockets"). It would become worth it if ElevenLabs withdrew v3/v4 from the text-to-speech endpoint, or if a listening comparison showed a difference for single-voice text; the protocol record is where that switch would go.
