# ElevenLabs Module

**Status:** Done

## Goal

Implement `ElevenLabsModule` in `modules/elevenlabs.py`: parse config, call the ElevenLabs streaming API with `output_format="pcm_44100"`, and fire the callback for each PCM chunk.

## Reference

See `specs/elevenlabs-module.md`.

## Tasks

### `modules/elevenlabs.py`

- [x] Define `ElevenLabsModule(TTSModule)`:
  - Constructor accepts the full `tts` config dict; parse and validate:
    - `api_key`: required, non-empty string → raise `ConfigError` if missing/empty
    - `voice_id`: required, non-empty string → raise `ConfigError` if missing/empty
    - `model`: optional, default `"eleven_flash_v2_5"`
    - `stability`: optional float, default `0.5`
    - `similarity_boost`: optional float, default `0.75`
  - Instantiate `elevenlabs.ElevenLabs(api_key=api_key)` client in constructor
- [x] Implement `stream(text, options, callback)`:
  - Call `self._client.text_to_speech.stream(...)` with:
    - `text=text`
    - `voice_id=self._voice_id`
    - `model_id=self._model`
    - `output_format="mp3_44100_128"`
    - `voice_settings=VoiceSettings(stability=..., similarity_boost=...)`
  - Feed non-empty MP3 chunks from the SDK iterator into `miniaudio.stream_any` for streaming decode to signed 16-bit PCM
  - Call `callback(pcm_chunk.tobytes())` for each decoded PCM chunk
  - The entire loop is synchronous — wrap with `asyncio.to_thread` to avoid blocking the event loop
  - Wrap all exceptions in `TTSError` with descriptive messages

### Register in `modules/__init__.py`

- [x] Import `ElevenLabsModule` and add `REGISTRY["elevenlabs"] = ElevenLabsModule`

### Tests (`tests/modules/test_elevenlabs.py`)

- [x] Constructor raises `ConfigError` for missing `api_key`
- [x] Constructor raises `ConfigError` for missing `voice_id`
- [x] Constructor uses correct defaults for `model`, `stability`, `similarity_boost`
- [x] `stream()` calls callback with each chunk yielded by the SDK iterator
- [x] `stream()` skips empty chunks (does not call callback)
- [x] `stream()` wraps SDK exception in `TTSError`

### Note on testing

Mock `elevenlabs.ElevenLabs` and its `text_to_speech.stream` method. Do not hit the real API in unit tests.
