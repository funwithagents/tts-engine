"""Tests for AudioPlayer and DiscardPlayer."""

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
# Driven on a virtual clock: `time` in tts_engine.audio is replaced by a fake
# whose sleep advances the clock, so the pace is asserted exactly and the tier
# never waits. `approx` only absorbs float rounding of the summed durations.

_RATE = 1000  # 1 sample = 1 ms


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr("tts_engine.audio.time", fake)
    return fake


def _ms_of_pcm(ms: int) -> bytes:
    return _pcm_bytes(ms * _RATE // 1000)


def test_discard_player_feeds_at_most_one_buffer_ahead_and_drain_plays_out(clock):
    player = DiscardPlayer(sample_rate=_RATE)
    fed = 0.0
    for _ in range(15):  # 15 x 20 ms = 0.3 s of audio
        player.feed(_ms_of_pcm(20))
        fed += 0.02
        # feed returned once the audio is at most one buffer ahead of the clock.
        assert clock.now == pytest.approx(max(0.0, fed - DISCARD_BUFFER_SECONDS))
    assert clock.now == pytest.approx(0.3 - DISCARD_BUFFER_SECONDS)

    player.drain()
    assert clock.now == pytest.approx(0.3)


def test_discard_player_paces_the_next_utterance_afresh_after_drain(clock):
    player = DiscardPlayer(sample_rate=_RATE)
    player.feed(_ms_of_pcm(200))
    player.drain()
    assert clock.now == pytest.approx(0.2)

    clock.now += 1.0  # idle between utterances
    player.feed(_ms_of_pcm(50))  # within the buffer: returns at once
    assert clock.now == pytest.approx(1.2)
    player.drain()
    assert clock.now == pytest.approx(1.25)


def test_discard_player_restarts_its_deadline_after_an_underrun(clock):
    player = DiscardPlayer(sample_rate=_RATE)
    player.feed(_ms_of_pcm(200))  # deadline 0.2, returns at 0.1
    assert clock.now == pytest.approx(0.1)

    clock.now = 0.5  # the producer is late: the first 0.2 s played out at 0.2
    player.feed(_ms_of_pcm(200))
    # Paced from now, not from the stale deadline: no burst, a real wait.
    assert clock.now == pytest.approx(0.6)
    player.drain()
    assert clock.now == pytest.approx(0.7)


def test_discard_player_ignores_empty_chunks(clock):
    player = DiscardPlayer(sample_rate=_RATE)
    player.feed(b"")
    player.drain()  # nothing fed: nothing to play out
    assert clock.sleeps == []

    player.feed(_ms_of_pcm(150))
    player.feed(b"")
    player.drain()
    assert clock.now == pytest.approx(0.15)
