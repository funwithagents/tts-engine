"""Tests for tts_engine.level: the level measure, the gain suggestion, and
`calibrate` through the `tone` fixture (a sine of known amplitude)."""

import numpy as np
import pytest

from tts_engine.config import ConfigError, TTSEngineConfig
from tts_engine.level import (
    DEFAULT_TEXTS,
    GainSuggestion,
    Level,
    calibrate,
    measure_level,
    suggest_gain,
)

_RATE = 24000


def _sine(amplitude: float, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(round(_RATE * seconds)) / _RATE
    return np.round(amplitude * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2")


# --- measure_level -------------------------------------------------------------


def test_measure_level_reads_the_active_speech_and_the_peak():
    silence = np.zeros(_RATE // 2, dtype="<i2")
    # A sine of amplitude 0.2: RMS 0.2/sqrt(2) (-17.0 dBFS), peak -14.0 dBFS;
    # the silence padding either end leaves the active level unchanged.
    padded = np.concatenate([silence, _sine(0.2), silence]).tobytes()
    assert measure_level(padded, _RATE) == Level(level_dbfs=-17.0, peak_dbfs=-14.0)


def test_measure_level_leaves_quiet_frames_out():
    # A tail far below -40 dBFS does not pull the level down, only the
    # duration grows.
    quiet = _sine(0.002, seconds=2.0)  # about -57 dBFS
    pcm = np.concatenate([_sine(0.2), quiet]).tobytes()
    assert measure_level(pcm, _RATE).level_dbfs == -17.0


def test_a_full_scale_peak_is_zero_not_minus_zero():
    peak = measure_level(_sine(1.0).tobytes(), _RATE).peak_dbfs
    assert peak == 0.0 and str(peak) == "0.0"


def test_measure_level_of_silence_and_of_nothing():
    silent = Level(level_dbfs=None, peak_dbfs=None)
    assert measure_level(np.zeros(_RATE, dtype="<i2").tobytes(), _RATE) == silent
    assert measure_level(b"", _RATE) == silent


@pytest.mark.parametrize(
    ("pcm", "rate", "message"), [(b"\x01\x00\x02", _RATE, "3 bytes"), (b"", 0, "> 0")]
)
def test_measure_level_rejects_bad_input(pcm, rate, message):
    with pytest.raises(ValueError, match=message):
        measure_level(pcm, rate)


# --- suggest_gain --------------------------------------------------------------


def test_suggest_gain_aims_at_the_target_and_reports_the_peak_after():
    loud = suggest_gain(Level(level_dbfs=-14.4, peak_dbfs=0.0), -22)
    assert loud == GainSuggestion(target_dbfs=-22, gain_db=-7.6, peak_after_dbfs=-7.6)
    assert not loud.clips

    quiet = suggest_gain(Level(level_dbfs=-22.7, peak_dbfs=-0.3), -18)
    assert (quiet.gain_db, quiet.peak_after_dbfs) == (4.7, 4.4)
    assert quiet.clips


def test_suggest_gain_stays_within_the_field_range():
    suggestion = suggest_gain(Level(level_dbfs=-59.0, peak_dbfs=-50.0), 0)
    assert (suggestion.gain_db, suggestion.limited) == (24.0, True)
    assert not suggest_gain(Level(level_dbfs=-20.0, peak_dbfs=-3.0)).limited


def test_suggest_gain_needs_speech_and_a_valid_target():
    with pytest.raises(ValueError, match="no active speech"):
        suggest_gain(Level(level_dbfs=None, peak_dbfs=None))
    for bad in (1.0, -61.0, float("nan")):
        with pytest.raises(ValueError, match="target_dbfs"):
            suggest_gain(Level(level_dbfs=-20.0, peak_dbfs=-3.0), bad)


# --- calibrate -----------------------------------------------------------------


def _tone(**fields) -> TTSEngineConfig:
    return TTSEngineConfig.from_dict({"module": {"type": "tone", **fields}})


@pytest.mark.asyncio
async def test_calibrate_measures_the_module_and_suggests_the_gain():
    result = await calibrate(_tone(seconds_per_char=0.01), target_dbfs=-20)
    # The tone's default amplitude is 0.2 of full scale.
    assert result.level == Level(level_dbfs=-17.0, peak_dbfs=-14.0)
    assert result.suggestion == GainSuggestion(
        target_dbfs=-20, gain_db=-3.0, peak_after_dbfs=-17.0
    )
    # Every built-in text was said: the tone lasts 0.01 s per character.
    chars = sum(len(t) for t in DEFAULT_TEXTS)
    assert result.duration_s == pytest.approx(chars * 0.01, abs=0.01)


@pytest.mark.asyncio
async def test_calibrate_ignores_the_gain_already_configured():
    configured = TTSEngineConfig.from_dict({"module": {"type": "tone"}, "gain_db": -12})
    result = await calibrate(configured, texts=["Hello there."])
    assert result.level.level_dbfs == -17.0
    assert result.duration_s == pytest.approx(len("Hello there.") * 0.05, abs=0.01)


@pytest.mark.asyncio
async def test_calibrate_rejects_bad_input_before_synthesizing():
    with pytest.raises(ValueError, match="non-empty text"):
        await calibrate(_tone(), texts=[])
    with pytest.raises(ValueError, match="non-empty text"):
        await calibrate(_tone(), texts=["  "])
    with pytest.raises(ValueError, match="target_dbfs"):
        await calibrate(_tone(), target_dbfs=3)
    with pytest.raises(ConfigError):
        await calibrate(_tone(amplitude=2.0))
