"""Tests for the utterance span, its events, and `UtteranceResult`
(specs/utterance-timing.md).

Driven through `TTSEngine` on the fixture modules, with the OpenTelemetry
SDK's in-memory exporter and a scripted clock: every read ticks one
nanosecond, so the stamps are strictly ordered and no test waits on real time.
"""

import asyncio
import itertools
import threading
import wave
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import tts_engine
from tts_engine import TTSEngine, TTSEngineConfig, UtteranceResult, UtteranceTiming
from tts_engine import utterance as utterance_module
from tts_engine.modules.base import feed_with_lookahead, segment_sentences
from tts_engine.utterance import UtteranceRecorder

_TONE = {"type": "tone"}
_SECOND = 1_000_000_000


class _Clock:
    """Stands in for the `time` module in `tts_engine.utterance`: one timeline
    for the wall and the monotonic clock, one tick per read, moved by hand."""

    BASE = 1_800_000_000 * _SECOND

    def __init__(self) -> None:
        self._ticks = itertools.count(1)
        self._offset = 0

    def advance(self, ns: int) -> None:
        self._offset += ns

    def perf_counter_ns(self) -> int:
        return next(self._ticks) + self._offset

    def time_ns(self) -> int:
        return self.BASE + self.perf_counter_ns()


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(utterance_module, "time", clock)
    return clock


@pytest.fixture
def exporter(monkeypatch) -> InMemorySpanExporter:
    """An SDK for the length of one test, as an application would install it."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return exporter


class _Sink:
    """An in-memory sink that notes the clock and the current span per feed."""

    def __init__(self, clock: _Clock | None = None, *, feed_ns: int = 0) -> None:
        self._clock = clock
        self._feed_ns = feed_ns
        self.chunks: list[bytes] = []
        self.fed_at: list[int] = []
        self.span_ids: set[int] = set()
        self.threads: set[int] = set()

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self.threads.add(threading.get_ident())
        self.span_ids.add(trace.get_current_span().get_span_context().span_id)
        if self._clock is not None:
            self.fed_at.append(self._clock.time_ns())
            self._clock.advance(self._feed_ns)

    def drain(self) -> None:
        pass


def _engine(module: dict, sink: object | None = None, **engine) -> TTSEngine:
    return TTSEngine(
        TTSEngineConfig.from_dict({"module": module, **engine}),
        sink=sink,  # type: ignore[arg-type]
    )


def _audiofile(tmp_path) -> dict:
    path = tmp_path / "default.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x01\x00" * 400)
    return {"type": "audiofile", "default_file": str(path)}


def _only(exporter: InMemorySpanExporter) -> ReadableSpan:
    spans = [s for s in exporter.get_finished_spans() if s.name != "caller"]
    assert len(spans) == 1
    return spans[0]


def _events(span: ReadableSpan) -> dict[str, int]:
    names = [event.name for event in span.events]
    assert len(names) == len(set(names)), names  # each event at most once
    return {event.name: event.timestamp for event in span.events}


def _attributes(span: ReadableSpan) -> dict:
    return dict(span.attributes or {})


def _ns(at: datetime | None) -> int:
    """A result's datetime back in nanoseconds, at its microsecond precision."""
    assert at is not None
    return int((at - datetime(1970, 1, 1, tzinfo=UTC)) / timedelta(microseconds=1)) * (
        1000
    )


# --- the span ----------------------------------------------------------------


@pytest.mark.parametrize("shape", ["say", "say_stream"])
async def test_a_call_is_one_span_child_of_the_callers(exporter, shape):
    engine = _engine(_TONE, _Sink())
    caller = trace.get_tracer("application")

    with caller.start_as_current_span("caller") as parent:
        if shape == "say":
            await engine.say("hello")
        else:
            await engine.say_stream(["hel", "lo"])

    span = _only(exporter)
    assert span.name == "tts_engine.utterance"
    assert span.instrumentation_scope is not None
    assert span.instrumentation_scope.name == "tts_engine"
    assert span.parent is not None
    assert span.parent.span_id == parent.get_span_context().span_id
    assert _attributes(span) == {
        "tts_engine.input": "text" if shape == "say" else "text_stream",
        "tts_engine.module": "tone",
        "tts_engine.module_kind": "fixture",
        "tts_engine.sample_rate": 24000,
        "tts_engine.text_chars": 5,
        "tts_engine.audio_seconds": pytest.approx(0.25),
        "tts_engine.outcome": "completed",
    }


async def test_the_span_is_current_in_the_worker_and_in_the_text_source(exporter):
    sink = _Sink()
    engine = _engine(_TONE, sink)
    seen_by_source: list[int] = []

    async def source():
        seen_by_source.append(trace.get_current_span().get_span_context().span_id)
        yield "hello"

    await engine.say_stream(source())

    context = _only(exporter).get_span_context()
    assert context is not None
    span_id = context.span_id
    assert sink.threads and threading.get_ident() not in sink.threads
    assert sink.span_ids == {span_id}
    assert seen_by_source == [span_id]


async def test_a_renamed_module_type_is_reported_under_its_current_name(
    exporter, mocker
):
    mocker.patch("tts_engine.engine.load_module", return_value=_engine(_TONE)._module)
    engine = _engine({"type": "openai-compatible"}, _Sink())

    await engine.say("hello")

    assert _attributes(_only(exporter))["tts_engine.module"] == (
        "openai-speech-compatible"
    )


# --- the events --------------------------------------------------------------


async def test_a_text_stream_stamps_every_step_in_order(exporter, clock, tmp_path):
    engine = _engine(_audiofile(tmp_path), _Sink())

    await engine.say_stream(["One sen", "tence. And ", "another one."])

    span = _only(exporter)
    events = _events(span)
    assert list(events) == [
        "admitted",
        "first_text",
        "first_segment",
        "first_audio",
        "last_audio",
    ]
    stamps = [span.start_time, *events.values(), span.end_time]
    assert stamps == sorted(stamps)
    assert events["first_audio"] < events["last_audio"]


async def test_a_complete_text_has_no_text_wait_to_report(exporter, clock, tmp_path):
    # audiofile cuts no sentence for a complete text; kokoro, piper and qwen3
    # do (`segment_sentences([text])`), and must not report a wait either.
    engine = _engine(_audiofile(tmp_path), _Sink())

    result = await engine.say("One sentence. And another one.")

    assert list(_events(_only(exporter))) == ["admitted", "first_audio", "last_audio"]
    assert result.timing.first_text_at is None
    assert result.timing.first_segment_at is None


def test_segmenting_a_complete_text_stamps_no_first_segment(exporter):
    with UtteranceRecorder(
        input="text", module="kokoro", module_kind="local", sample_rate=24000
    ):
        assert list(segment_sentences(["One. Two."])) == ["One.", "Two."]

    assert _events(_only(exporter)) == {}


async def test_a_module_that_does_not_segment_stamps_no_first_segment(exporter):
    engine = _engine(_TONE, _Sink())

    result = await engine.say_stream(["One sentence. ", "Another."])

    assert "first_segment" not in _events(_only(exporter))
    assert result.timing.first_text_at is not None
    assert result.timing.first_segment_at is None


def test_first_segment_is_stamped_from_the_lookahead_thread(exporter):
    # kokoro and piper pull their sentences in `feed_with_lookahead`'s producer,
    # a plain thread: it must still reach the utterance in flight.
    fed: list[bytes] = []
    with UtteranceRecorder(
        input="text_stream", module="kokoro", module_kind="local", sample_rate=24000
    ):
        feed_with_lookahead(
            segment_sentences(["One. ", "Two."]),
            lambda sentence: [sentence.encode()],
            fed.append,
            threading.Event(),
            slice_bytes=1024,
        )

    assert fed == [b"One.", b"Two."]
    assert "first_segment" in _events(_only(exporter))


async def test_first_and_last_audio_carry_the_time_of_their_feed(exporter, clock):
    # The tone module feeds from a worker thread; each feed takes a second.
    sink = _Sink(clock, feed_ns=_SECOND)
    engine = _engine(_TONE, sink)

    result = await engine.say("hello")

    events = _events(_only(exporter))
    assert len(sink.fed_at) >= 2
    assert 0 < sink.fed_at[0] - events["first_audio"] < 1000
    assert 0 < sink.fed_at[-1] - events["last_audio"] < 1000
    assert _ns(result.timing.first_audio_at) == events["first_audio"] // 1000 * 1000
    assert _ns(result.timing.last_audio_at) == events["last_audio"] // 1000 * 1000


async def test_a_late_first_piece_makes_first_text_late_by_as_much(exporter, clock):
    engine = _engine(_TONE, _Sink())

    async def source():
        clock.advance(3 * _SECOND)  # the writer thinks before its first word
        yield ""
        yield "hello"

    await engine.say_stream(source())

    events = _events(_only(exporter))
    waited = events["first_text"] - events["admitted"]
    assert 3 * _SECOND < waited < 3 * _SECOND + 1000
    assert events["first_audio"] > events["first_text"]


async def test_a_queued_call_is_admitted_after_the_one_in_flight_ends(exporter, clock):
    engine = _engine(_TONE, _Sink())
    speaking, release = asyncio.Event(), asyncio.Event()

    async def source():
        yield "hello"
        speaking.set()
        await release.wait()

    first = asyncio.create_task(engine.say_stream(source()))
    await speaking.wait()
    second = asyncio.create_task(engine.say("hi"))
    await asyncio.sleep(0)  # the second call starts, and queues on the lock
    clock.advance(5 * _SECOND)
    release.set()
    await asyncio.gather(first, second)

    in_flight, queued = sorted(
        exporter.get_finished_spans(), key=lambda s: s.start_time or 0
    )
    assert _attributes(queued)["tts_engine.input"] == "text"
    assert in_flight.end_time is not None and queued.start_time is not None
    assert queued.start_time < in_flight.end_time
    assert _events(queued)["admitted"] > in_flight.end_time
    # The wait behind the other utterance is the gap before `admitted`.
    assert _events(queued)["admitted"] - queued.start_time > 5 * _SECOND


# --- outcomes ----------------------------------------------------------------


async def test_cancelling_the_call_ends_the_span_as_cancelled(exporter):
    engine = _engine(_TONE, _Sink())
    speaking = asyncio.Event()

    async def source():
        yield "hello"
        speaking.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(engine.say_stream(source()))
    await speaking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    span = _only(exporter)
    assert _attributes(span)["tts_engine.outcome"] == "cancelled"
    assert span.status.status_code is StatusCode.UNSET
    assert "admitted" in _events(span)


async def test_a_call_cancelled_while_queued_was_never_admitted(exporter):
    engine = _engine(_TONE, _Sink())
    speaking, release = asyncio.Event(), asyncio.Event()

    async def source():
        yield "hello"
        speaking.set()
        await release.wait()

    first = asyncio.create_task(engine.say_stream(source()))
    await speaking.wait()
    queued = asyncio.create_task(engine.say_stream(["hi"]))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await first

    cancelled, completed = exporter.get_finished_spans()
    assert _attributes(cancelled)["tts_engine.outcome"] == "cancelled"
    assert _events(cancelled) == {}
    assert _attributes(completed)["tts_engine.outcome"] == "completed"


async def test_a_failure_ends_the_span_as_failed_with_its_exception(exporter):
    class _Broken(_Sink):
        def feed(self, chunk: bytes) -> None:
            super().feed(chunk)
            raise OSError("speaker unplugged")

    engine = _engine(_TONE, _Broken())

    with pytest.raises(OSError, match="speaker unplugged"):
        await engine.say("hello")

    span = _only(exporter)
    assert _attributes(span)["tts_engine.outcome"] == "failed"
    assert span.status.status_code is StatusCode.ERROR
    events = [event.name for event in span.events]
    assert events == ["admitted", "first_audio", "last_audio", "exception"]


async def test_a_closed_engine_fails_the_span_without_admitting(exporter):
    engine = _engine(_TONE, _Sink())
    await engine.close()

    with pytest.raises(RuntimeError, match="closed"):
        await engine.say("hello")

    span = _only(exporter)
    assert _attributes(span)["tts_engine.outcome"] == "failed"
    assert [event.name for event in span.events] == ["exception"]


# --- the default player ------------------------------------------------------


async def test_the_default_player_reports_when_playback_started(
    exporter, clock, mocker
):
    stream = mocker.MagicMock()
    stream.latency = 0.012
    mocker.patch("sounddevice.OutputStream", return_value=stream)
    engine = _engine(_TONE, gain_db=-6)

    result = await engine.say("hello")

    span = _only(exporter)
    events = _events(span)
    assert list(events) == [
        "admitted",
        "first_audio",
        "playback_started",
        "last_audio",
    ]
    assert events["first_audio"] < events["playback_started"]
    (started,) = [e for e in span.events if e.name == "playback_started"]
    assert dict(started.attributes or {}) == {
        "tts_engine.output_latency_seconds": 0.012
    }
    assert result.timing.output_latency_seconds == 0.012
    assert _ns(result.timing.playback_started_at) == (
        events["playback_started"] // 1000 * 1000
    )


async def test_a_device_reporting_no_latency_still_stamps_playback(exporter, mocker):
    stream = mocker.MagicMock()
    stream.latency = None
    mocker.patch("sounddevice.OutputStream", return_value=stream)
    engine = _engine(_TONE)

    result = await engine.say("hello")

    (started,) = [e for e in _only(exporter).events if e.name == "playback_started"]
    assert dict(started.attributes or {}) == {}
    assert result.timing.playback_started_at is not None
    assert result.timing.output_latency_seconds is None


async def test_no_other_sink_reports_playback(exporter):
    injected = _engine(_TONE, _Sink())
    # The discard player keeps real-time pace: five milliseconds of tone.
    brief = {"type": "tone", "seconds_per_char": 0.001}
    discarding = _engine(brief, player={"discard": True})

    results = [await injected.say("hello"), await discarding.say("hello")]

    for span, result in zip(exporter.get_finished_spans(), results, strict=True):
        assert "playback_started" not in _events(span)
        assert result.timing.playback_started_at is None
        assert result.timing.output_latency_seconds is None


# --- the result --------------------------------------------------------------


async def test_the_result_holds_the_spans_stamps_as_utc_datetimes(exporter, clock):
    sink = _Sink()
    engine = _engine(_TONE, sink)

    result = await engine.say_stream(["hel", "", "lo"])

    span = _only(exporter)
    events = _events(span)
    assert isinstance(result, UtteranceResult)
    assert result.text_chars == 5
    assert result.audio_seconds == len(b"".join(sink.chunks)) / 2 / 24000
    timing = result.timing
    assert isinstance(timing, UtteranceTiming)
    assert timing.started_at.tzinfo is UTC
    assert span.start_time is not None and span.end_time is not None
    assert _ns(timing.started_at) == span.start_time // 1000 * 1000
    assert _ns(timing.admitted_at) == events["admitted"] // 1000 * 1000
    assert _ns(timing.first_text_at) == events["first_text"] // 1000 * 1000
    assert _ns(timing.ended_at) == span.end_time // 1000 * 1000


async def test_without_an_sdk_the_result_is_the_same_data():
    # No tracer provider installed: the span is a no-op, the stamps are not.
    result = await _engine(_TONE, _Sink()).say("hello")

    timing = result.timing
    assert result.text_chars == 5
    assert result.audio_seconds == pytest.approx(0.25)
    assert timing.first_audio_at is not None and timing.last_audio_at is not None
    assert (
        timing.started_at
        <= timing.admitted_at
        <= timing.first_audio_at
        <= timing.last_audio_at
        <= timing.ended_at
    )
    assert abs(datetime.now(UTC) - timing.ended_at) < timedelta(minutes=1)


async def test_an_utterance_with_no_audio_has_no_audio_stamps(exporter):
    result = await _engine(_TONE, _Sink()).say_stream([])

    assert list(_events(_only(exporter))) == ["admitted"]
    assert result.text_chars == 0
    assert result.audio_seconds == 0
    assert result.timing.first_audio_at is None
    assert result.timing.last_audio_at is None


def test_the_result_types_are_top_level_exports():
    assert tts_engine.UtteranceResult is UtteranceResult
    assert tts_engine.UtteranceTiming is UtteranceTiming
    assert {"UtteranceResult", "UtteranceTiming"} <= set(tts_engine.__all__)
