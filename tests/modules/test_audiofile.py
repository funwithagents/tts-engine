"""Unit tests for the audiofile fixture module.

WAV fixtures are generated in `tmp_path` with the stdlib `wave` module, never
committed.
"""

import asyncio
import wave

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.audiofile import AudioFileModule
from tts_engine.modules.base import TTSError, TTSOptions


def _write_wav(path, *, frames=400, rate=8000, channels=1, sampwidth=2, fill=0):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sampwidth)
        wav.setframerate(rate)
        wav.writeframes(bytes([fill]) * (frames * channels * sampwidth))
    return str(path)


def _stream(module: AudioFileModule, text: str) -> bytes:
    received = bytearray()
    asyncio.run(module.stream(text, TTSOptions(), received.extend))
    return bytes(received)


# --- config validation -------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="missing.wav"):
        AudioFileModule(
            {"type": "audiofile", "default_file": str(tmp_path / "missing.wav")}
        )


def test_stereo_file_raises(tmp_path):
    path = _write_wav(tmp_path / "stereo.wav", channels=2)
    with pytest.raises(ConfigError, match="stereo.wav"):
        AudioFileModule({"type": "audiofile", "default_file": path})


def test_8bit_file_raises(tmp_path):
    path = _write_wav(tmp_path / "eight.wav", sampwidth=1)
    with pytest.raises(ConfigError, match="eight.wav"):
        AudioFileModule({"type": "audiofile", "default_file": path})


def test_mismatched_rates_raise(tmp_path):
    a = _write_wav(tmp_path / "a.wav", rate=8000)
    b = _write_wav(tmp_path / "b.wav", rate=16000)
    with pytest.raises(ConfigError, match="8000.*16000"):
        AudioFileModule(
            {
                "type": "audiofile",
                "files": [{"text": "a", "file": a}],
                "default_file": b,
            }
        )


def test_duplicate_text_raises(tmp_path):
    path = _write_wav(tmp_path / "a.wav")
    with pytest.raises(ConfigError, match="duplicate"):
        AudioFileModule(
            {
                "type": "audiofile",
                "files": [
                    {"text": "Hello,  world!", "file": path},
                    {"text": "Hello, world!", "file": path},
                ],
            }
        )


def test_malformed_entry_raises():
    with pytest.raises(ConfigError, match=r"files\[0\]"):
        AudioFileModule({"type": "audiofile", "files": [{"text": "a"}]})


def test_empty_config_raises():
    with pytest.raises(ConfigError, match="default_file"):
        AudioFileModule({"type": "audiofile"})


# --- behavior ----------------------------------------------------------------


def test_sample_rate_is_the_files_rate(tmp_path):
    path = _write_wav(tmp_path / "a.wav", rate=22050)
    assert AudioFileModule({"type": "audiofile", "default_file": path}).sample_rate == (
        22050
    )


def test_exact_match_plays_that_file(tmp_path):
    hello = _write_wav(tmp_path / "hello.wav", frames=5000, fill=1)
    other = _write_wav(tmp_path / "other.wav", frames=300, fill=2)
    module = AudioFileModule(
        {
            "type": "audiofile",
            "files": [{"text": "Hello, world!", "file": hello}],
            "default_file": other,
        }
    )
    # 5000 frames spans more than one 4096-frame chunk.
    assert _stream(module, "Hello, world!") == bytes([1]) * (2 * 5000)


def test_whitespace_normalized_text_matches(tmp_path):
    hello = _write_wav(tmp_path / "hello.wav", frames=400, fill=1)
    module = AudioFileModule(
        {"type": "audiofile", "files": [{"text": "Hello, world!", "file": hello}]}
    )
    assert _stream(module, "  Hello,   world! ") == bytes([1]) * (2 * 400)


def test_unmatched_text_plays_default_file(tmp_path):
    hello = _write_wav(tmp_path / "hello.wav", frames=400, fill=1)
    default = _write_wav(tmp_path / "default.wav", frames=250, fill=2)
    module = AudioFileModule(
        {
            "type": "audiofile",
            "files": [{"text": "Hello, world!", "file": hello}],
            "default_file": default,
        }
    )
    assert _stream(module, "something else") == bytes([2]) * (2 * 250)


def test_unmatched_text_without_default_raises_before_callback(tmp_path):
    hello = _write_wav(tmp_path / "hello.wav")
    module = AudioFileModule(
        {"type": "audiofile", "files": [{"text": "Hello, world!", "file": hello}]}
    )
    chunks: list[bytes] = []
    with pytest.raises(TTSError, match="no audio file configured"):
        asyncio.run(module.stream("Goodbye.", TTSOptions(), chunks.append))
    assert chunks == []


def test_relative_path_resolves_against_base_dir(tmp_path):
    _write_wav(tmp_path / "hello.wav", frames=100, rate=16000)
    module = AudioFileModule(
        {"type": "audiofile", "base_dir": str(tmp_path), "default_file": "hello.wav"}
    )
    assert module.sample_rate == 16000
    assert len(_stream(module, "anything")) == 2 * 100


def test_stream_does_not_mask_callback_errors(tmp_path):
    path = _write_wav(tmp_path / "a.wav")
    module = AudioFileModule({"type": "audiofile", "default_file": path})

    def bad_callback(_b):
        raise RuntimeError("playback failed")

    with pytest.raises(RuntimeError, match="playback failed"):
        asyncio.run(module.stream("hi", TTSOptions(), bad_callback))
