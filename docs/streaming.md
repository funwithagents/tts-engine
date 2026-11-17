# Speak an LLM response as it streams

`say_stream` takes an async iterable of text pieces and starts playing before the text is complete, so an LLM answer is heard as it is written rather than after the last token:

```python
async def llm_tokens():
    async with llm_client.messages.stream(...) as stream:  # any async token source
        async for token in stream.text_stream:
            yield token


await engine.say_stream(llm_tokens())
```

Pieces are concatenated verbatim (tokens carry their own spaces). The ElevenLabs, Gradium, Deepgram and Cartesia modules forward the text to their realtime sockets as it arrives; `pocket`, `phonon`, `qwen3`, `kokoro`, `piper`, `mistral`, `gemini`, `openai`, `openai-speech-compatible` and `audiofile` synthesize sentence by sentence as each one completes; `tone` plays a tone per piece. A plain list is accepted for in-memory sequences, and a bare string is rejected. If the text source raises, playback stops and that exception propagates unchanged. A long pause in the source is heard as silence, not an error.

## Errors and cancellation

These rules hold for `say()` as well as `say_stream()`.

```python
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError

try:
    async with TTSEngine(config) as engine:
        await engine.say("Hello from the TTS engine")
except ConfigError as exc:  # an invalid field, a missing extra or key
    print(f"configuration: {exc}")
except TTSError as exc:  # the backend failed: network, API error, bad response
    print(f"synthesis: {exc}")
```

- **`ConfigError`** (a `ValueError`) is raised by the config loaders and by `TTSEngine(config)`: an invalid field, a provider whose extra is not installed, a missing key.
- **`TTSError`** is raised when the backend fails, during an utterance or, for a backend that connects or loads a model up front, at construction.
- An exception raised by your text source, or by a custom sink's `feed()`, propagates unchanged.
- `RuntimeError` is raised by `say()` and `say_stream()` once the engine is closed.

Both exceptions are imported from their submodules, as above; they are not re-exported from `tts_engine`.

**Cancelling.** To interrupt speech (the user started talking), cancel the task that awaits the call:

```python
task = asyncio.create_task(engine.say_stream(llm_tokens()))
...
task.cancel()
```

When the `CancelledError` reaches you, the utterance is over: the sink gets no further `feed()`, it has been drained once, the text source has been closed (an async generator's `finally` has run), and the engine is ready for the next call. A cancel usually lands within a fraction of a second. It is not a guaranteed bound: a hosted backend blocked in a network call can take up to its `timeout`.
