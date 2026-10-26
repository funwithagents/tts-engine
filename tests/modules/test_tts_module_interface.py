import asyncio
import threading
from pathlib import Path

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules import REGISTRY, load_module
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    feed_with_lookahead,
    resolve_path,
    run_text_stream_worker,
    segment_sentences,
)


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


def test_tts_options_default_instantiation():
    TTSOptions()  # must not raise


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
    assert out == ["alpha beta gamma", "delta"]


def test_segment_sentences_never_cuts_inside_a_word():
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
