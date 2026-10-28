# Speak an LLM response as it streams

`say_stream` takes an async iterable of text pieces and starts playing before the text is complete, so an LLM answer is heard as it is written rather than after the last token:

```python
async def llm_tokens():
    async with llm_client.messages.stream(...) as stream:  # any async token source
        async for token in stream.text_stream:
            yield token


await engine.say_stream(llm_tokens())
```

Pieces are concatenated verbatim (tokens carry their own spaces). The ElevenLabs, Gradium, Deepgram and Cartesia modules forward the text to their realtime sockets as it arrives; `pocket`, `phonon`, `qwen3`, `kokoro`, `piper`, `mistral`, `gemini`, `openai`, `openai-compatible` and `audiofile` synthesize sentence by sentence as each one completes; `tone` plays a tone per piece. A plain list is accepted for in-memory sequences, and a bare string is rejected. If the text source raises, playback stops and that exception propagates unchanged. A long pause in the source is heard as silence, not an error.
