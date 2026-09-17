"""Unit tests for the tone fixture module (no fakes: it depends only on numpy)."""

import asyncio
import time

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSOptions
from tts_engine.modules.tone import ToneModule


def _stream(module: ToneModule, text: str) -> list[bytes]:
    chunks: list[bytes] = []
    asyncio.run(module.stream(text, TTSOptions(), chunks.append))
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


def test_default_sample_rate():
    assert ToneModule({"type": "tone"}).sample_rate == 24000


def test_configured_sample_rate_is_reported():
    assert ToneModule({"type": "tone", "sample_rate": 8000}).sample_rate == 8000


# --- streaming ---------------------------------------------------------------


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


def test_stream_does_not_mask_callback_errors():
    module = ToneModule({"type": "tone"})

    def bad_callback(_b):
        raise RuntimeError("playback failed")

    with pytest.raises(RuntimeError, match="playback failed"):
        asyncio.run(module.stream("hi", TTSOptions(), bad_callback))


async def test_cancel_stops_callbacks_before_stream_raises():
    module = ToneModule({"type": "tone"})

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        time.sleep(0.005)  # slow sink, so the stream is still running at cancel
        loop.call_soon_threadsafe(first_chunk.set)

    task = asyncio.create_task(module.stream("x" * 2000, TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
