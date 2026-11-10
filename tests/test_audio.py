"""Tests for AudioPlayer and DiscardPlayer."""

import time

import numpy as np
import pytest

from tts_engine.audio import DISCARD_BUFFER_SECONDS, AudioPlayer, DiscardPlayer


def _pcm_bytes(n_samples: int = 16) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def test_feed_opens_stream_once_at_the_given_sample_rate(mocker):
    mock_stream = mocker.MagicMock()
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)

    player = AudioPlayer(sample_rate=24000)
    player.feed(_pcm_bytes())
    player.feed(_pcm_bytes())

    mock_ctor.assert_called_once()
    assert mock_ctor.call_args.kwargs["samplerate"] == 24000
    assert mock_stream.start.call_count == 1
    assert mock_stream.write.call_count == 2


def test_drain_closes_stream(mocker):
    mock_stream = mocker.MagicMock()
    mocker.patch("sounddevice.OutputStream", return_value=mock_stream)

    player = AudioPlayer(sample_rate=44100)
    player.feed(_pcm_bytes())
    player.drain()

    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()

    # second drain must not raise
    player.drain()
    assert mock_stream.stop.call_count == 1


def test_feed_empty_bytes_does_not_open_stream(mocker):
    mock_ctor = mocker.patch("sounddevice.OutputStream")
    player = AudioPlayer(sample_rate=44100)
    player.feed(b"")
    mock_ctor.assert_not_called()


def test_start_failure_closes_stream_and_leaves_player_unopened(mocker):
    mock_stream = mocker.MagicMock()
    mock_stream.start.side_effect = RuntimeError("device busy")
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)
    player = AudioPlayer(sample_rate=44100)

    with pytest.raises(RuntimeError, match="device busy"):
        player.feed(_pcm_bytes())

    # The half-opened stream is released and nothing was written to it.
    mock_stream.close.assert_called_once()
    mock_stream.write.assert_not_called()
    # Nothing is attached: drain is a no-op and a later feed opens afresh.
    player.drain()
    mock_stream.stop.assert_not_called()
    mock_stream.start.side_effect = None
    player.feed(_pcm_bytes())
    assert mock_ctor.call_count == 2


def test_drain_closes_stream_even_if_stop_raises(mocker):
    mock_stream = mocker.MagicMock()
    mock_stream.stop.side_effect = RuntimeError("stop failed")
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)
    player = AudioPlayer(sample_rate=44100)
    player.feed(_pcm_bytes())

    with pytest.raises(RuntimeError, match="stop failed"):
        player.drain()

    # close() still ran, the stream is detached, and the player is reusable.
    mock_stream.close.assert_called_once()
    player.drain()  # no-op: nothing attached any more
    assert mock_stream.stop.call_count == 1
    player.feed(_pcm_bytes())  # a fresh stream is opened
    assert mock_ctor.call_count == 2


# --- DiscardPlayer: no device, real-time pace ------------------------------------
# Timings are real (the pace is the behavior under test) but short; the lower
# bounds are tight (a sink must never run ahead of real time), the upper ones
# generous for slow CI runners.

_RATE = 16000


def _seconds_of_pcm(seconds: float) -> bytes:
    return _pcm_bytes(int(_RATE * seconds))


def test_discard_player_paces_an_utterance_in_real_time():
    player = DiscardPlayer(sample_rate=_RATE)
    start = time.monotonic()
    for _ in range(15):  # 15 x 20 ms = 0.3 s of audio
        player.feed(_seconds_of_pcm(0.02))
    fed = time.monotonic() - start
    player.drain()
    total = time.monotonic() - start

    # feed ran at most one device buffer ahead; drain waited out the rest.
    assert 0.3 - DISCARD_BUFFER_SECONDS - 0.02 <= fed < 0.3
    assert 0.3 <= total < 0.3 + 0.25


def test_discard_player_drain_waits_out_the_remainder_then_starts_afresh():
    player = DiscardPlayer(sample_rate=_RATE)
    start = time.monotonic()
    player.feed(_seconds_of_pcm(0.2))  # one big chunk, like a whole-text backend
    assert time.monotonic() - start >= 0.2 - DISCARD_BUFFER_SECONDS
    player.drain()
    assert time.monotonic() - start >= 0.2

    # The next utterance is paced from its own start, not the previous one's.
    second = time.monotonic()
    player.feed(_seconds_of_pcm(0.15))
    player.drain()
    assert 0.15 <= time.monotonic() - second < 0.15 + 0.25

    # Nothing pending: a drain with nothing fed returns at once, as does an
    # empty chunk.
    idle = time.monotonic()
    player.feed(b"")
    player.drain()
    assert time.monotonic() - idle < 0.05
