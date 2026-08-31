---
code:
  - src/tts_engine/modules/elevenlabs.py
tests:
  - tests/modules/test_elevenlabs.py
  - tests-e2e/test_speak.py
---

# ElevenLabs Module

**Status:** Implemented

## Overview

`elevenlabs` implements `TTSModule` using the ElevenLabs streaming TTS API. It requests MP3 output and decodes each chunk to raw signed 16-bit PCM mono in-process before the callback, so `AudioPlayer` always receives PCM.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "elevenlabs"` (see [configuration.md](configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | string | yes | — | ElevenLabs API key. |
| `voice_id` | string | yes | — | The voice ID used for synthesis. |
| `model` | string | no | `"eleven_flash_v2_5"` | ElevenLabs model ID. `eleven_flash_v2_5` is recommended for low latency; `eleven_multilingual_v2` for quality. |
| `stability` | float | no | `0.5` | Voice stability (0.0–1.0). |
| `similarity_boost` | float | no | `0.75` | Similarity boost (0.0–1.0). |

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
    def _blocking_stream():
        try:
            raw_chunks = (
                chunk
                for chunk in self._client.text_to_speech.stream(
                    text=text,
                    voice_id=self._voice_id,
                    model_id=self._model,
                    output_format="mp3_44100_128",
                    voice_settings=VoiceSettings(
                        stability=self._stability,
                        similarity_boost=self._similarity_boost,
                    ),
                )
                if chunk
            )
            source = _ChunkSource(raw_chunks)
            for pcm_chunk in miniaudio.stream_any(
                source,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=44100,
            ):
                callback(pcm_chunk.tobytes())
        except Exception as exc:
            raise TTSError(f"ElevenLabs request failed: {exc}") from exc

    await asyncio.to_thread(_blocking_stream)
```

Note: the ElevenLabs SDK streaming method is synchronous (returns an iterator). The entire decode-and-feed loop is wrapped in `asyncio.to_thread` to avoid blocking the event loop.

### Dependencies

Requires `miniaudio` (`pip install miniaudio`) for MP3 → PCM streaming decoding.

### Error handling

- Any exception raised during streaming or decoding (SDK/API errors, auth failures, network errors, decode errors) is caught and re-raised as `TTSError(f"ElevenLabs request failed: {exc}")`, chained from the original via `raise ... from exc`. The original exception's message is preserved in the text, so auth (`401`) and network failures surface with their upstream detail without needing to be special-cased.

## Module ID

Registered in `REGISTRY` as `"elevenlabs"`.
