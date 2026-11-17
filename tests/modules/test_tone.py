"""Unit tests for the tone fixture module (no fakes: it depends only on numpy)."""

import asyncio
import math
import threading

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSOptions
from tts_engine.modules.tone import ToneModule


def _stream(module: ToneModule, text: str) -> list[bytes]:
    chunks: list[bytes] = []
    asyncio.run(module.synthesize_text(text, TTSOptions(), chunks.append))
    return chunks


# --- config validation -------------------------------------------------------


@pytest.mark.parametrize(
    "field, bad",
    [
        ("sample_rate", 0),
        ("sample_rate", True),
        ("sample_rate", "x"),
        ("frequency", 0),
        ("seconds_per_char", -1),
        ("amplitude", 0),
        ("amplitude", 1.5),
    ],
)
def test_invalid_field_raises_config_error(field, bad):
    with pytest.raises(ConfigError, match=field):
        ToneModule({"type": "tone", field: bad})


@pytest.mark.parametrize(
    "config, expected",
    [({}, 24000), ({"sample_rate": 8000}, 8000)],
    ids=["default", "configured"],
)
def test_sample_rate_is_reported(config, expected):
    assert ToneModule({"type": "tone", **config}).sample_rate == expected


# --- streaming ---------------------------------------------------------------

# Chunks in the default config's tone for 2000 chars (0.05 s/char at 24 kHz).
_TOTAL_CHUNKS = math.ceil(2000 * 0.05 * 24000 / 4096)


def test_stream_length_is_predictable():
    module = ToneModule({"type": "tone", "sample_rate": 8000, "seconds_per_char": 0.1})
    data = b"".join(_stream(module, "abcdefghij"))
    # 10 chars * 0.1 s * 8000 Hz = 8000 frames of int16
    assert len(data) == 2 * 8000
    assert len(data) % 2 == 0


def test_long_text_streams_in_several_chunks():
    chunks = _stream(ToneModule({"type": "tone"}), "x" * 200)
    assert len(chunks) > 1


def test_same_text_yields_identical_bytes():
    module = ToneModule({"type": "tone"})
    assert b"".join(_stream(module, "hello")) == b"".join(_stream(module, "hello"))


def _gated_callback(
    loop: asyncio.AbstractEventLoop,
    first_chunk: asyncio.Event,
    release: threading.Event,
):
    """A sink that signals the loop on the first chunk, then holds the worker
    there until `release` is set; `count()` reads how many chunks it got."""
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)
        release.wait(timeout=5)

    return callback, lambda: count


async def _cancel_while_held(
    task: asyncio.Task, first_chunk: asyncio.Event, release: threading.Event
) -> None:
    """Cancel `task` while its worker is held in the sink, then release it:
    the call must raise CancelledError."""
    try:
        await asyncio.wait_for(first_chunk.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)  # the cancel reaches the worker helper: `stop` is set
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


async def test_cancel_stops_callbacks_before_stream_raises():
    module = ToneModule({"type": "tone"})
    first_chunk = asyncio.Event()
    release = threading.Event()
    callback, count = _gated_callback(asyncio.get_running_loop(), first_chunk, release)

    task = asyncio.create_task(
        module.synthesize_text("x" * 2000, TTSOptions(), callback)
    )
    await _cancel_while_held(task, first_chunk, release)
    # The tone loop checks `stop` between chunks: the held chunk was the last
    # one fed, not the ~586 of the whole tone.
    assert count() <= 2 < _TOTAL_CHUNKS


# --- text stream -------------------------------------------------------------


def _stream_pieces(module: ToneModule, pieces: list[str]) -> list[bytes]:
    async def source():
        for piece in pieces:
            yield piece

    chunks: list[bytes] = []
    asyncio.run(module.synthesize_text_stream(source(), TTSOptions(), chunks.append))
    return chunks


def test_text_stream_is_phase_continuous_across_pieces():
    # The frame index runs on across pieces, so the same characters streamed in
    # pieces yield exactly the bytes of the complete text.
    module = ToneModule({"type": "tone", "sample_rate": 8000, "seconds_per_char": 0.1})
    assert b"".join(_stream_pieces(module, ["abc", "defgh"])) == b"".join(
        _stream(module, "abcdefgh")
    )


def test_empty_text_stream_feeds_nothing():
    assert _stream_pieces(ToneModule({"type": "tone"}), []) == []


async def test_text_stream_feeds_audio_before_the_source_finishes():
    # The source only yields its second piece once audio for the first has been
    # fed: a module that waited for the whole text would deadlock here.
    module = ToneModule({"type": "tone"})
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "first piece"
        await got_audio.wait()
        yield "second piece"

    chunks: list[bytes] = []

    def callback(chunk: bytes) -> None:
        chunks.append(chunk)
        loop.call_soon_threadsafe(got_audio.set)

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    # Default config: 0.05 s per char at 24 kHz, summed over the two pieces.
    expected_frames = sum(
        round(len(p) * 0.05 * 24000) for p in ("first piece", "second piece")
    )
    assert len(b"".join(chunks)) == 2 * expected_frames


async def test_text_stream_cancel_mid_audio_stops_callbacks_before_raising():
    module = ToneModule({"type": "tone"})
    first_chunk = asyncio.Event()
    release = threading.Event()
    callback, count = _gated_callback(asyncio.get_running_loop(), first_chunk, release)

    async def source():
        yield "x" * 2000  # ~100 s of tone: the audio outlasts the cancel
        await asyncio.Event().wait()  # and the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await _cancel_while_held(task, first_chunk, release)
    assert count() <= 2 < _TOTAL_CHUNKS
