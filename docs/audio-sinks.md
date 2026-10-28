# Audio output and custom sinks

Without a custom sink, `TTSEngine` constructs an `AudioPlayer` using `player.device` and the active module's sample rate. Playback starts lazily when the first PCM chunk arrives.

Applications that need to capture audio, send it across a network, or feed another playback system can inject an `AudioSink`-compatible object:

```python
class CaptureSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def drain(self) -> None:
        pass


sink = CaptureSink()
engine = TTSEngine(config, sink=sink)
await engine.say("Capture this instead of playing it")
```

The sink receives signed 16-bit little-endian mono PCM at `engine.sample_rate`. `feed()` calls are sequential but may happen off the event-loop thread. `drain()` is called once after every utterance, including when synthesis fails or is cancelled. When a custom sink is supplied, the engine does not construct or use the local audio player.
