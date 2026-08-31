---
code:
  - src/tts_engine/modules/elevenlabs.py
tests:
  - tests/modules/test_elevenlabs.py
  - tests-e2e/test_engine.py
  - tests-e2e/test_mcp.py
---

# ElevenLabs Module

**Status:** Implemented

## Overview

`elevenlabs` implements `TTSModule` using the ElevenLabs streaming TTS API. It requests MP3 output and incrementally decodes the encoded stream to raw signed 16-bit PCM mono in-process before the callback, so `AudioPlayer` always receives PCM.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "elevenlabs"` (see [configuration.md](configuration.md)).

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

The ElevenLabs SDK streaming interface yields `bytes` chunks of MP3 data. `miniaudio.stream_any` requires a `miniaudio.StreamableSource` (a `read(num_bytes)` interface), not a bare generator, so the MP3 chunk iterator is wrapped in a small `StreamableSource` adapter (`_ChunkSource`) that buffers chunks and serves the requested byte counts. `stream_any` decodes to PCM `array.array` chunks; call `callback(pcm_chunk.tobytes())` for each.

```python
class _ChunkSource(miniaudio.StreamableSource):
    """Wraps a bytes-chunk iterator as a miniaudio StreamableSource."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buf = bytearray()

    def read(self, num_bytes: int) -> bytes:
        while len(self._buf) < num_bytes:
            try:
                self._buf.extend(next(self._chunks))
            except StopIteration:
                break
        data = bytes(self._buf[:num_bytes])
        self._buf = self._buf[num_bytes:]
        return data


async def stream(self, text, options, callback):
    # Build and advance the provider/decoder iterator inside a worker thread.
    # Provider and decoder exceptions become TTSError. Invoke callback outside
    # that catch boundary so playback failures retain their real identity.
    ...
```

The ElevenLabs SDK streaming method is synchronous (returns an iterator). The entire decode-and-feed loop is wrapped in `asyncio.to_thread` to avoid blocking the event loop. On coroutine cancellation, a thread-safe stop flag requests termination between decoded chunks and the coroutine waits for the worker to finish before propagating `CancelledError`; no callback can occur after `stream()` exits.

### Dependencies

Requires `miniaudio` (`pip install miniaudio`) for MP3 → PCM streaming decoding.

### Error handling

- Any exception raised by the SDK or decoder (API errors, auth failures, network errors, invalid audio) is caught and re-raised as `TTSError(f"ElevenLabs request failed: {exc}")`, chained from the original via `raise ... from exc`.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as ElevenLabs request failures.
- Cancellation requests cooperative worker shutdown and waits until callback activity has stopped before propagating.

## Module ID

Registered in `REGISTRY` as `"elevenlabs"`.
