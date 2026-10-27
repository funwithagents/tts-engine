import asyncio
import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules import REGISTRY, base, load_module
from tts_engine.modules.base import (
    PCMFeeder,
    TTSError,
    TTSModule,
    cleanup,
    feed_with_lookahead,
    finite_number,
    float_to_int16_pcm,
    open_cancellable,
    optional_str,
    resolve_api_key,
    resolve_path,
    run_cancellable_worker,
    run_text_stream_worker,
    segment_sentences,
    slice_bytes_for,
    slice_text,
)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # The helpers poll their stop events and queues; a short interval keeps
    # the cancel and give-up paths from waiting on wall-clock ticks.
    monkeypatch.setattr(base, "_POLL_SECONDS", 0.005)


def test_load_module_unknown_type_raises():
    with pytest.raises(ConfigError):
        load_module({"type": "nonexistent_xyz"})


def test_load_module_registered_type():
    class StubModule(TTSModule):
        def __init__(self, config: dict):
            self.config = config

        @property
        def sample_rate(self) -> int:
            return 44100

        async def synthesize_text(self, text, options, callback):
            pass

        async def synthesize_text_stream(self, text_stream, options, callback):
            pass

    REGISTRY["_stub_test"] = StubModule
    try:
        result = load_module({"type": "_stub_test", "extra": "value"})
        assert isinstance(result, StubModule)
        assert result.config == {"type": "_stub_test", "extra": "value"}
    finally:
        del REGISTRY["_stub_test"]


# --- resolve_path ------------------------------------------------------------


def test_resolve_path_absolute_returned_unchanged(tmp_path):
    absolute = tmp_path / "voice.wav"
    assert resolve_path({"base_dir": "/other"}, str(absolute)) == absolute


def test_resolve_path_relative_joins_base_dir():
    config = {"type": "x", "base_dir": "/some/dir"}
    assert resolve_path(config, "sub/a.wav") == Path("/some/dir/sub/a.wav")


def test_resolve_path_relative_without_base_dir_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_path({"type": "x"}, "a.wav") == Path.cwd() / "a.wav"


# --- segment_sentences -------------------------------------------------------


@pytest.mark.parametrize(
    "pieces, expected",
    [
        (["Hello. ", "World"], ["Hello.", "World"]),
        (
            ["Hel", "lo world! How", " are you? Fine"],
            ["Hello world!", "How are you?", "Fine"],
        ),
        (
            ["Pi is 3.14 and v2.5 ships. ", "Next"],
            ["Pi is 3.14 and v2.5 ships.", "Next"],
        ),
        (["First line\nSecond line"], ["First line", "Second line"]),
        (['He said "Go." Then left'], ['He said "Go."', "Then left"]),
        (["   ", "", "Only one"], ["Only one"]),
        (["Done."], ["Done."]),
        ([], []),
    ],
)
def test_segment_sentences_cuts_at_sentence_ends(pieces, expected):
    assert list(segment_sentences(pieces)) == expected


def test_segment_sentences_forces_a_cut_past_max_chars_at_a_clause_boundary():
    out = list(segment_sentences(["alpha beta, gamma delta epsilon"], max_chars=20))
    assert out == ["alpha beta,", "gamma delta epsilon"]


def test_segment_sentences_forces_a_cut_at_whitespace_without_clause_boundary():
    out = list(segment_sentences(["alpha ", "beta ", "gamma ", "delta"], max_chars=12))
    assert out == ["alpha beta", "gamma delta"]


def test_segment_sentences_bounds_segments_however_the_input_is_chunked():
    # One big piece (a complete text) and the same text character by character
    # must both respect the bound: the cut is found inside the window, not at
    # the last boundary anywhere in the buffer.
    text = "word " * 2000
    whole = list(segment_sentences([text]))
    by_char = list(segment_sentences(list(text)))
    assert whole == by_char
    assert all(len(segment) <= 200 for segment in whole)
    assert " ".join(whole) == text.strip()


def test_segment_sentences_prefers_a_sentence_end_inside_the_window():
    # The first sentence end lies past max_chars: the cut comes earlier, at
    # the last whitespace inside the window, so no segment exceeds the bound.
    out = list(segment_sentences(["aaa bbb ccc. ddd"], max_chars=8))
    assert out == ["aaa bbb", "ccc.", "ddd"]


def test_segment_sentences_lets_only_a_long_word_exceed_the_bound():
    # A word longer than the bound is never cut inside, whether it arrives
    # with its boundary in the same piece or the boundary comes later.
    out = list(segment_sentences(["x" * 30 + " y"], max_chars=10))
    assert out == ["x" * 30, "y"]
    out = list(segment_sentences(["x" * 300, " tail."], max_chars=20))
    assert out == ["x" * 300, "tail."]


def test_segment_sentences_yields_a_sentence_before_consuming_the_next_piece():
    consumed: list[str] = []

    def pieces():
        for piece in ["Hello. ", "World"]:
            consumed.append(piece)
            yield piece

    gen = segment_sentences(pieces())
    assert next(gen) == "Hello."
    assert consumed == ["Hello. "]


# --- run_text_stream_worker --------------------------------------------------


def _blocking_source(closed: list[bool]):
    """A source that yields one piece, then blocks until cancelled; records
    (in `closed`) that its finally block ran."""

    async def source():
        try:
            yield "a"
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    return source()


async def test_text_stream_worker_receives_pieces_in_order_without_empties():
    async def source():
        for piece in ["a", "", "b", "c"]:
            yield piece

    seen: list[str] = []
    await run_text_stream_worker(source(), lambda pieces, stop: seen.extend(pieces))
    assert seen == ["a", "b", "c"]


async def test_text_stream_worker_source_error_aborts_and_propagates_unchanged():
    loop = asyncio.get_running_loop()
    got_first = asyncio.Event()

    async def source():
        yield "a"
        await got_first.wait()
        raise RuntimeError("llm failed")

    seen: list[str] = []
    stopped_at_exit = False

    def worker(pieces, stop):
        nonlocal stopped_at_exit
        for piece in pieces:
            seen.append(piece)
            loop.call_soon_threadsafe(got_first.set)
        stopped_at_exit = stop.is_set()
        # A backend seeing its text end abruptly may fail; the source's error
        # is still the one reported.
        raise TTSError("stream ended early")

    with pytest.raises(RuntimeError, match="llm failed"):
        await run_text_stream_worker(source(), worker)
    assert seen == ["a"]
    assert stopped_at_exit is True


async def test_text_stream_worker_failure_closes_the_source_and_propagates():
    closed: list[bool] = []

    def worker(pieces, stop):
        next(pieces)
        raise TTSError("boom")

    with pytest.raises(TTSError, match="boom"):
        await run_text_stream_worker(_blocking_source(closed), worker)
    assert closed == [True]


async def test_text_stream_worker_returning_early_closes_the_source():
    closed: list[bool] = []

    def worker(pieces, stop):
        next(pieces)

    await run_text_stream_worker(_blocking_source(closed), worker)
    assert closed == [True]


async def test_text_stream_worker_cancel_waits_for_the_thread_then_raises():
    loop = asyncio.get_running_loop()
    closed: list[bool] = []
    started = asyncio.Event()
    exited = False

    def worker(pieces, stop):
        nonlocal exited
        for _piece in pieces:  # ends when stop is set
            loop.call_soon_threadsafe(started.set)
        exited = True

    task = asyncio.create_task(run_text_stream_worker(_blocking_source(closed), worker))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited is True
    assert closed == [True]


async def test_cancellable_worker_repeated_cancel_still_waits_for_the_thread():
    # A second cancel while the helper is already waiting for the thread must
    # not cut that wait short: the engine would drain the sink and release its
    # lock while the worker can still call back.
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    feed_done = threading.Event()
    exited = False

    def worker(stop):
        nonlocal exited
        loop.call_soon_threadsafe(started.set)
        stop.wait()
        feed_done.wait(5)  # a feed in progress: `stop` is honored only after it
        exited = True

    task = asyncio.create_task(run_cancellable_worker(worker))
    await started.wait()
    task.cancel()
    for _ in range(5):  # the helper is now waiting for the thread
        await asyncio.sleep(0)
    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done()  # the second cancel did not cut the wait short
    feed_done.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert exited is True


async def test_text_stream_worker_source_cancelled_error_is_a_source_failure():
    # A CancelledError raised by the source itself (not the bridge's own cancel
    # of its pump) aborts the utterance and propagates, like any source error,
    # instead of the bridge reporting a complete, successful utterance.
    loop = asyncio.get_running_loop()
    got_first = asyncio.Event()
    seen: list[str] = []
    stopped_at_exit = False

    async def source():
        yield "a"
        await got_first.wait()
        raise asyncio.CancelledError

    def worker(pieces, stop):
        nonlocal stopped_at_exit
        for piece in pieces:
            seen.append(piece)
            loop.call_soon_threadsafe(got_first.set)
        stopped_at_exit = stop.is_set()

    with pytest.raises(asyncio.CancelledError):
        await run_text_stream_worker(source(), worker)
    assert seen == ["a"]
    assert stopped_at_exit is True


# --- feed_with_lookahead -----------------------------------------------------


def test_lookahead_feeds_every_chunk_in_order_in_slices():
    fed: list[bytes] = []
    feed_with_lookahead(
        ["a", "b"],
        lambda s: [s.encode() * 5, s.encode() * 2],
        fed.append,
        threading.Event(),
        slice_bytes=2,
    )
    assert fed == [b"aa", b"aa", b"a", b"aa", b"bb", b"bb", b"b", b"bb"]


def test_lookahead_synthesizes_the_next_sentence_while_feeding():
    started = {s: threading.Event() for s in ("one", "two")}

    def synthesize(sentence: str) -> list[bytes]:
        started[sentence].set()
        return [b"xx"]

    overlapped: list[bool] = []

    def callback(_chunk: bytes) -> None:
        if not overlapped:
            overlapped.append(started["two"].wait(timeout=2))

    feed_with_lookahead(
        ["one", "two"], synthesize, callback, threading.Event(), slice_bytes=2
    )
    assert overlapped == [True]


def test_lookahead_raises_a_synthesis_error_unchanged():
    def synthesize(sentence: str) -> list[bytes]:
        if sentence == "bad":
            raise TTSError("boom")
        return [b"xx"]

    fed: list[bytes] = []
    with pytest.raises(TTSError, match="boom"):
        feed_with_lookahead(
            ["ok", "bad", "never"],
            synthesize,
            fed.append,
            threading.Event(),
            slice_bytes=2,
        )
    assert fed == [b"xx"]


def test_lookahead_propagates_a_callback_error():
    def callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        feed_with_lookahead(
            ["a"], lambda s: [b"xx"], callback, threading.Event(), slice_bytes=2
        )


def test_lookahead_stop_ends_feeding_and_further_synthesis():
    stop = threading.Event()
    calls: list[str] = []
    fed: list[bytes] = []

    def synthesize(sentence: str) -> list[bytes]:
        calls.append(sentence)
        return [b"x" * 100]

    def callback(chunk: bytes) -> None:
        fed.append(chunk)
        stop.set()  # cancelled while the first slice plays

    feed_with_lookahead(
        [str(i) for i in range(10)], synthesize, callback, stop, slice_bytes=10
    )
    assert fed == [b"x" * 10]
    assert len(calls) <= 2  # the one played and at most one ahead


# --- run_text_stream_worker: the bounded bridge -----------------------------


async def test_text_stream_worker_bounds_how_far_the_source_runs_ahead():
    # An in-memory source yields without ever suspending: unbounded, the pump
    # would consume it entirely before the worker ran once. With the bound the
    # pump waits for the worker, so the source is read at the worker's pace.
    produced = 0
    produced_when_first_taken: list[int] = []
    taken = 0

    async def source():
        nonlocal produced
        for _ in range(200):
            produced += 1
            yield "x" * 100

    def worker(pieces, stop):
        nonlocal taken
        for _piece in pieces:
            if not produced_when_first_taken:
                produced_when_first_taken.append(produced)
            taken += 1

    await run_text_stream_worker(source(), worker, max_pending_chars=500)
    assert taken == 200  # everything still arrives
    # At most: five queued (the bound), the one the worker just took (which
    # frees room for a sixth) and a seventh the pump holds while it waits for
    # room again, since the source counts a piece before yielding it. Never
    # the 200 an unbounded pump would have read.
    assert produced_when_first_taken[0] <= 7


async def test_text_stream_worker_accepts_one_piece_larger_than_the_bound():
    seen: list[str] = []
    await run_text_stream_worker(
        _as_source(["x" * 50, "y"]),
        lambda pieces, stop: seen.extend(pieces),
        max_pending_chars=10,
    )
    assert seen == ["x" * 50, "y"]


async def test_text_stream_worker_cancel_with_an_endless_immediate_source():
    # A source that never suspends and never ends must not keep the loop (and
    # the cancel) from running: the bound makes the pump yield.
    loop = asyncio.get_running_loop()
    started = asyncio.Event()

    async def source():
        while True:
            yield "x"

    def worker(pieces, stop):
        for _piece in pieces:
            loop.call_soon_threadsafe(started.set)
            time.sleep(0.001)  # slower than the source

    task = asyncio.create_task(run_text_stream_worker(source(), worker))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


async def test_text_stream_worker_cancel_with_an_endless_empty_only_source():
    # Empty pieces never reach the queue, so the bound alone would not make
    # the pump yield: it yields on every empty piece instead.
    async def source():
        while True:
            yield ""

    def worker(pieces, stop):
        for _piece in pieces:  # never gets one; ends when stop is set
            pass

    task = asyncio.create_task(run_text_stream_worker(source(), worker))
    for _ in range(10):  # the pump is spinning on empty pieces
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


def _as_source(pieces: list[str]):
    async def source():
        for piece in pieces:
            yield piece

    return source()


# --- cleanup -----------------------------------------------------------------


def test_cleanup_keeps_the_blocks_error_over_a_failing_close(caplog):
    def close():
        raise OSError("close failed")

    with (
        pytest.raises(ValueError, match="primary"),
        caplog.at_level("WARNING"),
        cleanup(close, "thing"),
    ):
        raise ValueError("primary")
    assert "thing: cleanup failed" in caplog.text


def test_cleanup_keeps_cancellation_over_a_failing_close():
    def close():
        raise OSError("close failed")

    with pytest.raises(asyncio.CancelledError), cleanup(close, "thing"):
        raise asyncio.CancelledError


def test_cleanup_raises_a_failing_close_when_the_block_succeeded():
    def close():
        raise OSError("close failed")

    with pytest.raises(OSError, match="close failed"), cleanup(close, "thing"):
        pass


# --- close hook --------------------------------------------------------------


def test_close_defaults_to_a_no_op():
    class Minimal(TTSModule):
        @property
        def sample_rate(self) -> int:
            return 8000

        async def synthesize_text(self, text, options, callback) -> None:
            pass

        async def synthesize_text_stream(self, text_stream, options, callback) -> None:
            pass

    module = Minimal({"type": "minimal"})
    module.close()
    module.close()  # nothing to release, nothing to break


# --- open_cancellable --------------------------------------------------------


class _Resource:
    def __init__(self) -> None:
        self.left = threading.Event()


def test_open_cancellable_returns_what_enter_returned():
    resource = _Resource()
    stop = threading.Event()

    got = open_cancellable(
        lambda: resource, lambda r: r.left.set(), stop=stop, timeout=1, what="x"
    )

    assert got is resource
    assert not resource.left.is_set()


def test_open_cancellable_raises_enters_error_unchanged():
    def enter():
        raise ConnectionError("refused")

    with pytest.raises(ConnectionError, match="refused"):
        open_cancellable(
            enter, lambda r: None, stop=threading.Event(), timeout=1, what="x"
        )


def test_open_cancellable_gives_up_on_stop_and_releases_the_late_resource():
    resource = _Resource()
    release = threading.Event()
    stop = threading.Event()

    def enter():
        release.wait(5)
        return resource

    stop.set()
    started = time.perf_counter()
    got = open_cancellable(
        enter, lambda r: r.left.set(), stop=stop, timeout=5, what="x"
    )
    assert got is None
    assert time.perf_counter() - started < 1  # not the timeout, not enter's wait

    release.set()  # the handshake completes after the caller has gone
    assert resource.left.wait(1)


def test_open_cancellable_times_out_without_stop():
    release = threading.Event()

    def enter():
        release.wait(5)
        return _Resource()

    with pytest.raises(TimeoutError, match="not open within"):
        open_cancellable(
            enter, lambda r: None, stop=threading.Event(), timeout=0.01, what="x"
        )
    release.set()


# --- slice_text --------------------------------------------------------------


def test_slice_text_cuts_at_sentence_ends_and_concatenates_back():
    text = "One two. Three four! Five six seven eight nine."
    slices = list(slice_text(text, 20))
    assert slices == ["One two. ", "Three four! ", "Five six seven ", "eight nine."]
    assert "".join(slices) == text


def test_slice_text_short_text_is_one_slice_and_a_long_word_passes_whole():
    assert list(slice_text("short", 20)) == ["short"]
    assert list(slice_text("a" * 30 + " tail", 20)) == ["a" * 20, "a" * 10 + " tail"]


# --- shared config helpers ---------------------------------------------------

_LABEL = "X module"


def test_resolve_api_key_prefers_the_literal_over_the_variable(monkeypatch):
    monkeypatch.setenv("TTS_TEST_KEY", "from-env")
    both = {"api_key": "literal", "api_key_env": "TTS_TEST_KEY"}
    assert resolve_api_key(both, label=_LABEL) == "literal"
    env_only = {"api_key": "", "api_key_env": "TTS_TEST_KEY"}
    assert resolve_api_key(env_only, label=_LABEL) == "from-env"


@pytest.mark.parametrize("value", [None, ""])
def test_resolve_api_key_names_an_unset_or_empty_variable(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TTS_TEST_KEY", raising=False)
    else:
        monkeypatch.setenv("TTS_TEST_KEY", value)
    with pytest.raises(
        ConfigError, match="X module: environment variable 'TTS_TEST_KEY'"
    ):
        resolve_api_key({"api_key_env": "TTS_TEST_KEY"}, label=_LABEL)


@pytest.mark.parametrize(
    "config", [{}, {"api_key": ""}, {"api_key": None, "api_key_env": None}]
)
def test_resolve_api_key_requires_one_of_the_fields(config):
    with pytest.raises(
        ConfigError, match="X module requires a non-empty 'api_key' or 'api_key_env'"
    ):
        resolve_api_key(config, label=_LABEL)


@pytest.mark.parametrize(
    "config, field",
    [({"api_key": 5}, "api_key"), ({"api_key_env": ["K"]}, "api_key_env")],
)
def test_resolve_api_key_rejects_a_non_string(config, field):
    with pytest.raises(ConfigError, match=f"X module '{field}' must be a string"):
        resolve_api_key(config, label=_LABEL)


def test_optional_str_absent_null_and_default_policy():
    assert optional_str({}, "f", label=_LABEL) is None
    assert optional_str({"f": None}, "f", label=_LABEL) is None  # null is absent
    assert optional_str({"f": "v"}, "f", label=_LABEL) == "v"
    assert optional_str({}, "f", label=_LABEL, default="d") == "d"
    assert optional_str({"f": "v"}, "f", label=_LABEL, default="d") == "v"
    message = "X module 'f' must be a non-empty string"
    for config in ({"f": ""}, {"f": 3}):
        with pytest.raises(ConfigError, match=message):
            optional_str(config, "f", label=_LABEL)
    # With a default, an explicit null is a value the config names: rejected.
    with pytest.raises(ConfigError, match=message):
        optional_str({"f": None}, "f", label=_LABEL, default="d")


def test_finite_number_absent_null_and_default_policy():
    assert finite_number({}, "f", label=_LABEL) is None
    assert finite_number({"f": None}, "f", label=_LABEL) is None
    value = finite_number({"f": 2}, "f", label=_LABEL)
    assert value == 2.0 and isinstance(value, float)
    assert finite_number({}, "f", label=_LABEL, default=30) == 30.0
    with pytest.raises(ConfigError, match="X module 'f' must be a finite number"):
        finite_number({"f": None}, "f", label=_LABEL, default=30)


@pytest.mark.parametrize(
    "bad", [True, "1", float("nan"), float("inf"), -float("inf"), [1]]
)
def test_finite_number_rejects_non_numbers_bools_and_non_finite(bad):
    with pytest.raises(ConfigError, match="X module 'f' must be a finite number"):
        finite_number({"f": bad}, "f", label=_LABEL)


@pytest.mark.parametrize(
    "bounds, ok, bad, message",
    [
        ({"exclusive_minimum": 0}, 0.5, 0, "a finite number > 0"),
        ({"minimum": 1.0}, 1.0, 0.99, "a finite number >= 1"),
        ({"maximum": 4.0}, 4, 4.5, "a finite number <= 4"),
        ({"minimum": 0.0, "maximum": 1.0}, 1, 1.01, "a finite number between 0 and 1"),
        (
            {"exclusive_minimum": 0, "maximum": 1.0},
            1.0,
            0,
            "a finite number > 0 and <= 1",
        ),
    ],
)
def test_finite_number_bounds_and_their_message(bounds, ok, bad, message):
    assert finite_number({"f": ok}, "f", label=_LABEL, **bounds) == float(ok)
    with pytest.raises(ConfigError, match=re.escape(f"X module 'f' must be {message}")):
        finite_number({"f": bad}, "f", label=_LABEL, **bounds)


# --- shared PCM helpers --------------------------------------------------------


def test_float_to_int16_pcm_scales_clips_and_flattens_without_touching_the_input():
    samples = np.array([[0.0, 0.5], [-1.0, 2.0]], dtype=np.float64)
    pcm = float_to_int16_pcm(samples)
    assert np.frombuffer(pcm, "<i2").tolist() == [0, 16383, -32767, 32767]
    assert samples[1, 1] == 2.0
    assert float_to_int16_pcm([1.0, -2.0]) == b"\xff\x7f\x01\x80"


def test_slice_bytes_for_is_100ms_of_int16_mono():
    assert slice_bytes_for(24000) == 4800
    assert slice_bytes_for(22050) == 4410
    assert slice_bytes_for(1) == 2


def test_pcm_feeder_aligns_across_chunks_and_feeds_in_slices():
    fed: list[bytes] = []
    feeder = PCMFeeder(fed.append, threading.Event(), slice_bytes=4)
    raw = bytes(range(1, 12))  # five samples and one odd byte
    assert feeder.feed(raw[:3]) is True  # one whole sample; the third byte waits
    assert feeder.feed(raw[3:]) is True
    assert fed == [raw[0:2], raw[2:6], raw[6:10]]  # whole samples, at most 4 bytes
    assert b"".join(fed) == raw[:10]  # the trailing odd byte is never fed


def test_pcm_feeder_converts_whole_samples_before_slicing():
    fed: list[bytes] = []
    floats = np.array([0.5, -0.5, 1.0], dtype="<f4").tobytes()
    feeder = PCMFeeder(
        fed.append,
        threading.Event(),
        slice_bytes=4,
        sample_bytes=4,
        convert=lambda data: float_to_int16_pcm(np.frombuffer(data, "<f4")),
    )
    assert feeder.feed(floats[:5]) is True  # one float32 sample and a byte
    assert feeder.feed(floats[5:]) is True
    assert [np.frombuffer(c, "<i2").tolist() for c in fed] == [[16383], [-16383, 32767]]


def test_pcm_feeder_stops_between_slices_and_feeds_nothing_after():
    stop = threading.Event()
    fed: list[bytes] = []

    def callback(chunk: bytes) -> None:
        fed.append(chunk)
        stop.set()

    feeder = PCMFeeder(callback, stop, slice_bytes=2)
    assert feeder.feed(bytes(range(6))) is False
    assert fed == [b"\x00\x01"]
    assert feeder.feed(b"\x06\x07") is False
    assert fed == [b"\x00\x01"]
