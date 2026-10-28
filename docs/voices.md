# Finding a voice

`list_voices` returns the voices a backend offers, read from its own catalog. It takes the same `module` block as the engine but needs no voice, so the credentials are enough:

```python
from tts_engine import list_voices

voices = list_voices({"type": "cartesia", "api_key_env": "CARTESIA_API_KEY"})
men = [v for v in voices if v.gender == "male" and "en" in v.languages]
config = {"type": "cartesia", "api_key_env": "CARTESIA_API_KEY", **men[0].config}
```

Each `VoiceInfo` carries an `id`, a `name`, and `config`, the module fields that select it (`{"voice_id": ...}`, `{"voice": ...}`, `{"model": ...}` for Deepgram, `{"voice": ..., "speaker": ...}` for a multi-speaker Piper voice). It also has `languages`, a `gender` normalized to `male` / `female` / `neutral` when the backend gives one, the backend's own `age` wording, a `description` and `tags`.

The call blocks while it reaches the provider or downloads a catalog (qwen3 loads its model), so async code runs it with `asyncio.to_thread`. Gemini, OpenAI Realtime and `/v1/audio/speech` have no listing endpoint: for them it raises `VoiceListingUnsupported`, whose message links to the page that lists their voices. The fixtures list none.
