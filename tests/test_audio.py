"""Tests for AudioPlayer."""

import numpy as np
import pytest

from tts_engine.audio import AudioPlayer, AudioSink


def _pcm_bytes(n_samples: int = 16) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def test_audio_player_is_an_audio_sink():
    # Static + structural conformance: AudioPlayer satisfies the AudioSink seam
    # embedders type their own destinations against.
    sink: AudioSink = AudioPlayer(sample_rate=44100)
    assert callable(sink.feed)
    assert callable(sink.drain)


def test_feed_opens_stream_once(mocker):
    mock_stream = mocker.MagicMock()
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)

    player = AudioPlayer(sample_rate=44100)
    player.feed(_pcm_bytes())
    player.feed(_pcm_bytes())

    mock_ctor.assert_called_once()
    assert mock_stream.start.call_count == 1
    assert mock_stream.write.call_count == 2


def test_feed_opens_stream_at_given_sample_rate(mocker):
    mock_ctor = mocker.patch("sounddevice.OutputStream")

    AudioPlayer(sample_rate=24000).feed(_pcm_bytes())

    assert mock_ctor.call_args.kwargs["samplerate"] == 24000


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


def test_drain_before_feed_does_not_raise(mocker):
    mocker.patch("sounddevice.OutputStream")
    player = AudioPlayer(sample_rate=44100)
    player.drain()  # must not raise


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
