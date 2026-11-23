# Measure what the listener waited for

Every `say()` and `say_stream()` call reports when each of its steps happened: when it stopped waiting behind another utterance, when the first text arrived, when the first audio reached the sink, when the device started playing. The same stamps come in two forms: the value the call returns, and an OpenTelemetry span. Design and guarantees: [specs/utterance-timing.md](../specs/utterance-timing.md).

## The result of a call

Both methods return an `UtteranceResult` when they complete:

```python
result = await engine.say("Hello from the TTS engine")

timing = result.timing
if timing.first_audio_at is not None:
    time_to_first_audio = timing.first_audio_at - timing.started_at
    print(f"first audio after {time_to_first_audio.total_seconds() * 1000:.0f} ms")
print(f"{result.text_chars} characters, {result.audio_seconds:.2f} s of audio")
```

| Field | Meaning |
|---|---|
| `result.text_chars` | characters of text consumed |
| `result.audio_seconds` | seconds of audio fed to the sink |
| `timing.started_at` | the call started |
| `timing.admitted_at` | the utterance started: the engine speaks one utterance at a time, and before this the call waited for the one in flight |
| `timing.first_text_at` | `say_stream` only: the first non-empty piece was taken from the text stream |
| `timing.first_segment_at` | `say_stream` only, on a backend that speaks sentence by sentence: the first sentence was complete and handed to synthesis |
| `timing.first_audio_at`, `timing.last_audio_at` | the first and the last chunk reached the sink |
| `timing.playback_started_at` | default player only: the first chunk was written to the sound device |
| `timing.output_latency_seconds` | default player only: how long the device says a written sample takes to be heard |
| `timing.ended_at` | the call ended: the sink was drained |

Times are timezone-aware UTC datetimes. A step that did not happen is `None`: no audio was produced, the call was a `say`, the sink is your own. The result is returned only when the call completes; a call that fails or is cancelled raises as before, and its stamps are on the span.

Useful differences:

| Duration | What it tells you |
|---|---|
| `admitted_at − started_at` | the wait behind another utterance |
| `first_text_at − admitted_at` | how long the text source took to write its first word |
| `first_segment_at − first_text_at` | how long it took to finish its first sentence: a slow writer, not a slow voice |
| `first_audio_at − first_segment_at` (or `− first_text_at`, or `− admitted_at` for `say`) | synthesis: the backend's time to first audio |
| `playback_started_at − first_audio_at` | opening the sound device |
| `playback_started_at + output_latency_seconds` | an estimate of when sound left the speaker |

With a [custom sink](audio-sinks.md), the engine cannot know when your destination starts playing: `first_audio_at` is when the first chunk reached your `feed()`, and what happens after is yours to measure.

## The span

Each call is also one [OpenTelemetry](https://opentelemetry.io/docs/languages/python/) span. `tts-engine` depends on the OpenTelemetry API only, so nothing is recorded or sent until your application installs the SDK and an exporter:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
```

The span is a child of whatever span is current where you await the call, so an utterance appears inside the trace of the request that caused it, with no identifier to pass:

```python
tracer = trace.get_tracer("my_application")

with tracer.start_as_current_span("answer the user"):
    await engine.say_stream(llm_tokens())  # its span is a child of "answer the user"
```

These names are stable; a trace reader can rely on them.

| Kind | Name | Meaning |
|---|---|---|
| Tracer | `tts_engine` | every span the package opens |
| Span | `tts_engine.utterance` | one `say()` or `say_stream()` call, from the call to its end |
| Event | `admitted` | `admitted_at` |
| Event | `first_text` | `first_text_at` |
| Event | `first_segment` | `first_segment_at` |
| Event | `first_audio` | `first_audio_at` |
| Event | `last_audio` | `last_audio_at` |
| Event | `playback_started` | `playback_started_at`; carries `tts_engine.output_latency_seconds` when the device reports it |
| Attribute | `tts_engine.input` | `text` (`say`) or `text_stream` (`say_stream`) |
| Attribute | `tts_engine.module` | the module's type, such as `pocket` |
| Attribute | `tts_engine.module_kind` | `hosted`, `local` or `fixture` |
| Attribute | `tts_engine.sample_rate` | Hz |
| Attribute | `tts_engine.text_chars` | characters of text consumed |
| Attribute | `tts_engine.audio_seconds` | seconds of audio fed to the sink |
| Attribute | `tts_engine.outcome` | `completed`, `failed` or `cancelled` |

An event is present only when its step happened, and at most once. The span ends on every path: a failed call ends it as `failed`, with the exception recorded and the span's status set to error; a cancelled call ends it as `cancelled`. A call cancelled while it was still waiting for another utterance has no `admitted` event.

The span is current while the engine works, including when it pulls text from your source: a span your text source opens (an LLM request) is a child of `tts_engine.utterance`.
