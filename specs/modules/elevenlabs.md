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

`synthesize_text_stream` is a **true stream-to-stream** pass: it does not group the text into sentences. The SDK exposes the ElevenLabs *stream-input* WebSocket (`wss://…/v1/text-to-speech/{voice_id}/stream-input`) as `client.text_to_speech.convert_realtime(voice_id, *, text: Iterator[str], model_id, output_format, voice_settings) -> Iterator[bytes]`: a **synchronous** generator that pulls text pieces from the given iterator, sends them over the socket as they arrive, and yields MP3 chunks as the server returns them. The SDK's own `text_chunker` buffers the pieces to word/punctuation boundaries before sending, so sub-word LLM tokens never reach the server split.

That shape fits the existing worker exactly: the module runs its blocking loop through `run_text_stream_worker` ([tts-module-interface.md](../tts-module-interface.md), "Text-stream helpers"), hands the bridge's blocking `pieces` iterator to `convert_realtime` as `text`, and feeds the resulting MP3 iterator through the **same** `_ChunkSource` + `miniaudio.stream_any` decode as `synthesize_text`, with the same `output_format="mp3_44100_128"`, `model_id` and `voice_settings`. The two methods share one decode-and-feed loop (`_decode_and_feed(raw_chunks, callback, stop)`): advance the decoder inside the `TTSError` boundary, call `callback` outside it, poll `stop` between chunks. Only the SDK call differs. `sample_rate` stays 44100.

Error and cancellation rules are unchanged: SDK/socket/decoder failures become `TTSError("ElevenLabs request failed: …")`; callback failures propagate unchanged; a text-stream failure aborts via the bridge and propagates unchanged; cancellation stops the worker before `synthesize_text_stream` raises.

**Known limitation, accepted for v1.** The SDK's generator polls the socket for audio only once per text piece sent, with a 10 ms timeout, then drains the rest after the final (empty) text message. With a slow text source, audio can therefore lag behind what the server has already produced until the stream ends. Measured on the live tier (plan `202610261140_streaming-text-input`): with a source paced at 60 ms per word, first audio arrived 0.7 s into the stream and the last audio 1.0 s after the last piece — no lag worth replacing the SDK loop for. See "Open questions".

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

1. **Own WebSocket loop for the text stream.** Not needed on current measurements (above). If a future SDK or a slower source ever shows a real audio lag, replace `convert_realtime` with a two-thread loop over the same socket — one sending text as it arrives, one receiving audio continuously — using `websockets.sync`, which the `elevenlabs` SDK already depends on (no new extra). Same decode path, same contract; only the transport loop changes.
2. **PCM output for the stream.** The stream-input endpoint also serves `pcm_*` formats, which would drop the MP3 decode; MP3 is kept so both methods share one decode path and one declared rate.
