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

`elevenlabs` implements `TTSModule` using the ElevenLabs streaming TTS API. It requests MP3 output and incrementally decodes the encoded stream to raw signed 16-bit PCM mono in-process before the callback, so `AudioPlayer` always receives PCM. It is one API-backed provider among others — not the default and not the base install — and ships behind the `elevenlabs` packaging extra (see "Dependencies").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "elevenlabs"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | ElevenLabs API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key. Lets a config file be committed with no secret. |
| `voice_id` | non-empty string | yes | — | The voice ID used for synthesis. |
| `model` | non-empty string | no | `"eleven_flash_v2_5"` | ElevenLabs model ID. `eleven_flash_v2_5` is recommended for low latency; `eleven_multilingual_v2` for quality. |
| `stability` | number | no | `0.5` | Voice stability (0.0–1.0). |
| `similarity_boost` | number | no | `0.75` | Similarity boost (0.0–1.0). |

### API key resolution

The key is resolved in this order: a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither `api_key` nor `api_key_env` yields a key, construction raises `ConfigError`. Prefer `api_key_env` so `config.json` can be committed without a secret.

`voice_id` and `model` are validated as non-empty strings. `stability` and `similarity_boost` are validated at construction as numeric values in the inclusive range 0–1; booleans are rejected rather than treated as integers. Invalid module configuration therefore fails as `ConfigError` before the SDK client is used.

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

`synthesize_text_stream` is a **true stream-to-stream** pass: it does not group the text into sentences. It drives the ElevenLabs *stream-input* WebSocket (`wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=…&output_format=mp3_44100_128`, authenticated with the `xi-api-key` header) with the module's **own loop**, over `websockets.sync`, which the SDK already depends on — not the SDK's `convert_realtime` (why not: below).

The worker comes from `run_text_stream_worker` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers") and runs two threads over one connection:

- **Sender** (a daemon thread) — pulls the bridge's `pieces`, groups them at **word boundaries** with `split_at_last_space` (so a sub-word LLM token is never sent alone), sends each group as `{"text": …, "try_trigger_generation": true}`, then the remainder and the end-of-stream message `{"text": ""}`. If `stop` ends the iterator early, no end-of-stream is sent: the request is dropped.
- **Receiver** (the worker thread, as a generator of MP3 chunks) — opens the socket, sends the opening message (`text: " "` with the `voice_settings` and `generation_config.chunk_length_schedule = [50]`, the API's minimum), then receives continuously with a **100 ms poll** (`recv(timeout=0.1)`), checking `stop` and the sender's error on each wake, decoding each message's base64 `audio`, until the server's `isFinal`, a normal close, `stop`, or a failure on either side.

The MP3 chunks feed the **same** `_ChunkSource` + `miniaudio.stream_any` decode as `synthesize_text` through one `_decode_and_feed(open_raw, callback, stop)` loop (open the raw source and build the decoder inside the `try/finally` that closes the raw source, so a decoder failure releases it too; advance the decoder inside the `TTSError` boundary, call `callback` outside it, poll `stop` between chunks). The loop peeks the first raw chunk before building the decoder: **no audio means no decoder** and no callback — miniaudio cannot initialize a decoder on empty input, so the decision has to come first. `sample_rate` stays 44100.

An **empty stream** (no non-empty piece) sends no text and no end-of-stream: the sender notes it, the receiver leaves the `with connect(...)` block without waiting for `isFinal` (dropping the connection), and with no raw audio nothing is decoded or fed — the interface's "an exhausted stream with no text produces no audio and is not an error". The socket is still opened as soon as the worker starts, so the handshake overlaps the wait for the first piece rather than following it.

Error and cancellation rules are unchanged: connection, socket and decoder failures — on either thread — become `TTSError("ElevenLabs request failed: …")`; callback failures propagate unchanged; a text-stream failure aborts via the bridge and propagates unchanged; cancellation stops the receiver within one poll interval, closes the socket (leaving the `with connect(...)` block), and the thread exits before `synthesize_text_stream` raises. A sender still blocked on text is released when `stop` reaches `pieces`; its late send on the closed socket is ignored.

**Why not the SDK's `convert_realtime`.** It pulls text and audio on one thread: for each text piece it sends, it polls the socket once with a 10 ms timeout, and only after the text iterator ends does it drain the rest. When the text source pauses — an LLM mid-answer — it blocks on the next piece and receives **nothing**, so playback stalls for the length of the pause and a cancel cannot be honored until text resumes. Found by the live cancellation scenario in plan `202610261310_cancellation-tests-and-elevenlabs-stream-loop`; with a continuously fed source the two loops measure the same (first audio ~0.7 s into a 60 ms/word stream).

### Dependencies and lazy import

Requires the `elevenlabs` extra: `pip install tts-engine[elevenlabs]` / `uv sync --extra elevenlabs`, declared in `[project.optional-dependencies]` as `elevenlabs = ["elevenlabs", "miniaudio"]` — the official SDK plus `miniaudio` for the MP3 → PCM streaming decode. Neither is a base dependency (see [project.md](../project.md), "Dependency strategy for TTS backends").

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
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as ElevenLabs request failures.
- Cancellation requests cooperative worker shutdown and waits until callback activity has stopped before propagating.

## Module ID

Registered in `REGISTRY` as `"elevenlabs"`.

## Open questions

Deferrals only (design is settled):

1. **PCM output for the stream.** The stream-input endpoint also serves `pcm_*` formats, which would drop the MP3 decode; MP3 is kept so both methods share one decode path and one declared rate.
