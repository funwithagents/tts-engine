"""Utterance timing: one OpenTelemetry span per `say`/`say_stream` call, its
events, and the same stamps as plain data (`UtteranceResult`).

The names below are a public contract (specs/utterance-timing.md): a trace
reader finds an utterance by its tracer and span name, and reads its steps
from the event names.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

TRACER_NAME = "tts_engine"
SPAN_NAME = "tts_engine.utterance"

EVENT_ADMITTED = "admitted"
EVENT_FIRST_TEXT = "first_text"
EVENT_FIRST_SEGMENT = "first_segment"
EVENT_FIRST_AUDIO = "first_audio"
EVENT_LAST_AUDIO = "last_audio"
EVENT_PLAYBACK_STARTED = "playback_started"

ATTR_INPUT = "tts_engine.input"
ATTR_MODULE = "tts_engine.module"
ATTR_MODULE_KIND = "tts_engine.module_kind"
ATTR_SAMPLE_RATE = "tts_engine.sample_rate"
ATTR_TEXT_CHARS = "tts_engine.text_chars"
ATTR_AUDIO_SECONDS = "tts_engine.audio_seconds"
ATTR_OUTCOME = "tts_engine.outcome"
ATTR_OUTPUT_LATENCY_SECONDS = "tts_engine.output_latency_seconds"

UtteranceInput = Literal["text", "text_stream"]
UtteranceOutcome = Literal["completed", "failed", "cancelled"]

_SAMPLE_BYTES = 2


@dataclass(frozen=True)
class UtteranceTiming:
    """When each step of an utterance happened, as UTC datetimes.

    A step that did not happen is `None`: `first_text_at` and
    `first_segment_at` for a complete text (`say`) — and `first_segment_at`
    for a module that does not group its text into sentences —,
    `first_audio_at` and `last_audio_at` when no audio was produced,
    `playback_started_at` and `output_latency_seconds` for any sink but the
    default player.
    """

    started_at: datetime
    admitted_at: datetime
    first_text_at: datetime | None
    first_segment_at: datetime | None
    first_audio_at: datetime | None
    last_audio_at: datetime | None
    playback_started_at: datetime | None
    output_latency_seconds: float | None
    ended_at: datetime


@dataclass(frozen=True)
class UtteranceResult:
    """What one completed `say`/`say_stream` call consumed and produced."""

    text_chars: int
    audio_seconds: float
    timing: UtteranceTiming


# The utterance running in the current context, for the stamps taken below the
# engine (`note_segment`). `asyncio.to_thread` and `create_task` copy the
# context; a plain `threading.Thread` does not and must be started with one.
_current: contextvars.ContextVar[UtteranceRecorder | None] = contextvars.ContextVar(
    "tts_engine_utterance", default=None
)


def note_segment() -> None:
    """Stamp `first_segment` on the utterance of the current context, if any.
    Called by `segment_sentences` each time it hands a segment on."""
    recorder = _current.get()
    if recorder is not None:
        recorder.segment()


def _to_datetime(ns: int) -> datetime:
    seconds, rest = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=rest // 1000)


class UtteranceRecorder:
    """The span and the stamps of one `say`/`say_stream` call.

    Created when the call starts, in the caller's context (the span is a child
    of the span current there). Used as a context manager around the whole
    call: inside, the span is current and `note_segment` reaches this
    recorder; on exit the span is ended with the outcome the exit shows.

    Stamps are wall-clock: one `time.time_ns()` read when the call starts,
    plus monotonic offsets from it, so they compare with other packages'
    stamps and never run backwards inside an utterance. Each event is added
    with its explicit timestamp on the span object held here, never on "the
    current span", so a stamp taken from any thread lands on the right span.
    """

    def __init__(
        self,
        *,
        input: UtteranceInput,
        module: str,
        module_kind: str,
        sample_rate: int,
    ) -> None:
        self._wall_start = time.time_ns()
        self._mono_start = time.perf_counter_ns()
        self._input = input
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._stamps: dict[str, int] = {}
        self._last_audio: int | None = None
        self._ended: int | None = None
        self._audio_bytes = 0
        self._text_chars = 0
        self._output_latency: float | None = None
        self._span_token: contextvars.Token[otel_context.Context] | None = None
        self._current_token: contextvars.Token[UtteranceRecorder | None] | None = None
        self._span = trace.get_tracer(TRACER_NAME).start_span(
            SPAN_NAME,
            start_time=self._wall_start,
            attributes={
                ATTR_INPUT: input,
                ATTR_MODULE: module,
                ATTR_MODULE_KIND: module_kind,
                ATTR_SAMPLE_RATE: sample_rate,
            },
        )

    def _now(self) -> int:
        return self._wall_start + time.perf_counter_ns() - self._mono_start

    def _mark(
        self, name: str, at: int | None = None, attributes: dict | None = None
    ) -> None:
        """Stamp `name` once: the first call wins, from whichever thread."""
        if name in self._stamps:
            return
        with self._lock:
            if name in self._stamps:
                return
            at = self._now() if at is None else at
            self._stamps[name] = at
            self._span.add_event(name, attributes=attributes, timestamp=at)

    def __enter__(self) -> Self:
        self._span_token = otel_context.attach(trace.set_span_in_context(self._span))
        self._current_token = _current.set(self)
        return self

    def __exit__(
        self, _exc_type: object, exc: BaseException | None, _tb: object
    ) -> None:
        assert self._span_token is not None and self._current_token is not None
        _current.reset(self._current_token)
        otel_context.detach(self._span_token)
        self._finish(exc)

    def admitted(self) -> None:
        """The say lock is held and the engine is open: the utterance starts."""
        self._mark(EVENT_ADMITTED)

    def text(self, text: str) -> None:
        """The complete text of a `say`."""
        self._text_chars += len(text)

    def text_stream(self, source: AsyncIterator[str]) -> AsyncIterator[str]:
        """`source`, counted: `first_text` when its first non-empty piece is
        taken. Closing stays with the owner of `source`."""
        return _CountedText(source, self)

    def piece(self, piece: str) -> None:
        if piece:
            self._mark(EVENT_FIRST_TEXT)
            self._text_chars += len(piece)

    def segment(self) -> None:
        # A complete text has no wait for its first sentence to report.
        if self._input == "text_stream":
            self._mark(EVENT_FIRST_SEGMENT)

    def feeder(
        self,
        feed: Callable[[bytes], None],
        output_latency: Callable[[], float | None] | None = None,
    ) -> Callable[[bytes], None]:
        """`feed`, stamped: `first_audio` and `last_audio` when a non-empty
        chunk reaches it. With `output_latency` (the default player's),
        `playback_started` once the first such chunk has been written."""

        def stamped(chunk: bytes) -> None:
            if not chunk:
                feed(chunk)
                return
            now = self._now()
            self._mark(EVENT_FIRST_AUDIO, now)
            self._last_audio = now
            self._audio_bytes += len(chunk)
            feed(chunk)
            if (
                output_latency is not None
                and EVENT_PLAYBACK_STARTED not in self._stamps
            ):
                latency = output_latency()
                self._output_latency = latency
                self._mark(
                    EVENT_PLAYBACK_STARTED,
                    attributes=None
                    if latency is None
                    else {ATTR_OUTPUT_LATENCY_SECONDS: latency},
                )

        return stamped

    def _audio_seconds(self) -> float:
        return self._audio_bytes / _SAMPLE_BYTES / self._sample_rate

    def _finish(self, exc: BaseException | None) -> None:
        self._ended = self._now()
        if self._last_audio is not None:
            self._span.add_event(EVENT_LAST_AUDIO, timestamp=self._last_audio)
        outcome: UtteranceOutcome
        if exc is None:
            outcome = "completed"
        elif isinstance(exc, asyncio.CancelledError):
            outcome = "cancelled"
        else:
            outcome = "failed"
            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, str(exc)))
        self._span.set_attributes(
            {
                ATTR_TEXT_CHARS: self._text_chars,
                ATTR_AUDIO_SECONDS: self._audio_seconds(),
                ATTR_OUTCOME: outcome,
            }
        )
        self._span.end(end_time=self._ended)

    def result(self) -> UtteranceResult:
        """The stamps as plain data, once the utterance has completed."""
        assert self._ended is not None

        def at(name: str) -> datetime | None:
            ns = self._stamps.get(name)
            return None if ns is None else _to_datetime(ns)

        return UtteranceResult(
            text_chars=self._text_chars,
            audio_seconds=self._audio_seconds(),
            timing=UtteranceTiming(
                started_at=_to_datetime(self._wall_start),
                admitted_at=_to_datetime(self._stamps[EVENT_ADMITTED]),
                first_text_at=at(EVENT_FIRST_TEXT),
                first_segment_at=at(EVENT_FIRST_SEGMENT),
                first_audio_at=at(EVENT_FIRST_AUDIO),
                last_audio_at=None
                if self._last_audio is None
                else _to_datetime(self._last_audio),
                playback_started_at=at(EVENT_PLAYBACK_STARTED),
                output_latency_seconds=self._output_latency,
                ended_at=_to_datetime(self._ended),
            ),
        )


class _CountedText:
    """An async iterator over a text source that reports each piece taken."""

    def __init__(self, source: AsyncIterator[str], recorder: UtteranceRecorder) -> None:
        self._source = source
        self._recorder = recorder

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> str:
        piece = await self._source.__anext__()
        self._recorder.piece(piece)
        return piece
