"""Tests for AudioPlayer."""
import numpy as np

from tts_engine.audio import AudioPlayer


def _pcm_bytes(n_samples: int = 16) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def test_feed_opens_stream_once(mocker):
    mock_stream = mocker.MagicMock()
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)

    player = AudioPlayer()
    player.feed(_pcm_bytes())
    player.feed(_pcm_bytes())

    mock_ctor.assert_called_once()
    assert mock_stream.start.call_count == 1
    assert mock_stream.write.call_count == 2


def test_drain_closes_stream(mocker):
    mock_stream = mocker.MagicMock()
    mocker.patch("sounddevice.OutputStream", return_value=mock_stream)

    player = AudioPlayer()
    player.feed(_pcm_bytes())
    player.drain()

    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()

    # second drain must not raise
    player.drain()
    assert mock_stream.stop.call_count == 1


def test_drain_before_feed_does_not_raise(mocker):
    mocker.patch("sounddevice.OutputStream")
    player = AudioPlayer()
    player.drain()  # must not raise


def test_feed_empty_bytes_does_not_open_stream(mocker):
    mock_ctor = mocker.patch("sounddevice.OutputStream")
    player = AudioPlayer()
    player.feed(b"")
    mock_ctor.assert_not_called()
