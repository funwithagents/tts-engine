---
code:
  - src/tts_mcp/modules/elevenlabs.py
tests:
  - tests/modules/test_elevenlabs.py
  - tests-e2e/test_speak.py
---

# ElevenLabs Module

**Status:** Implemented

## Overview

`elevenlabs` implements `TTSModule` using the ElevenLabs streaming TTS API. It requests raw PCM output so chunks can be fed directly to `AudioPlayer` without decoding.

## Config fields

All fields go under the `tts` block in `config.json` alongside `"type": "elevenlabs"`.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | string | yes | — | ElevenLabs API key. |
| `voice_id` | string | yes | — | Default voice ID used when `TTSOptions.voice_id` is `None`. |
| `model` | string | no | `"eleven_flash_v2_5"` | ElevenLabs model ID. `eleven_flash_v2_5` is recommended for low latency; `eleven_multilingual_v2` for quality. |
| `stability` | float | no | `0.5` | Voice stability (0.0–1.0). |
| `similarity_boost` | float | no | `0.75` | Similarity boost (0.0–1.0). |

## Implementation notes

### SDK vs raw HTTP

Use the official `elevenlabs` Python SDK. It provides a `generate` function (non-streaming) and a `stream` function for streaming. Use the streaming interface.

### Output format and decoding

Request `output_format="mp3_44100_128"` from the ElevenLabs API (128 kbps MP3 at 44100 Hz). MP3 chunks are then decoded to raw signed 16-bit PCM mono using `miniaudio.stream_any` before being passed to the callback — so `AudioPlayer` always receives PCM regardless of the upstream format.

### Streaming

The ElevenLabs SDK streaming interface yields `bytes` chunks of MP3 data. Feed them into a `miniaudio.stream_any` source generator, which decodes them to PCM `array.array` chunks. Call `callback(pcm_chunk.tobytes())` for each decoded chunk.

```python
async def stream(self, text, options, callback):
    def _blocking_stream():
        def _mp3_source():
            for chunk in elevenlabs_client.text_to_speech.stream(
                text=text,
                voice_id=self._voice_id,
                model_id=self._model,
                output_format="mp3_44100_128",
                voice_settings=VoiceSettings(
                    stability=self._stability,
                    similarity_boost=self._similarity_boost,
                ),
            ):
                if chunk:
                    yield chunk

        for pcm_chunk in miniaudio.stream_any(
            _mp3_source(),
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=44100,
        ):
            callback(pcm_chunk.tobytes())

    await asyncio.to_thread(_blocking_stream)
```

Note: the ElevenLabs SDK streaming method is synchronous (returns an iterator). The entire decode-and-feed loop is wrapped in `asyncio.to_thread` to avoid blocking the event loop.

### Dependencies

Requires `miniaudio` (`pip install miniaudio`) for MP3 → PCM streaming decoding.

### Error handling

- Wrap SDK exceptions in `TTSError` with a descriptive message.
- `401` / auth errors → raise `TTSError("ElevenLabs authentication failed — check api_key")`
- Network errors → raise `TTSError("ElevenLabs request failed: <original message>")`

## Module ID

Registered in `REGISTRY` as `"elevenlabs"`.
