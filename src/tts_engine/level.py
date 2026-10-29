"""Speech level measurement and `gain_db` calibration.

`measure_level` reads the active level and the peak of int16 mono PCM;
`suggest_gain` turns a level into the engine `gain_db` that brings it to a
target; `calibrate` renders built-in (or given) texts through an engine and
does both. Design: specs/level.md.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from tts_engine.config import GAIN_DB_MAX, GAIN_DB_MIN, TTSEngineConfig
from tts_engine.engine import TTSEngine

# The active-speech measure: the RMS of the 20 ms frames above -40 dBFS, so the
# silence an engine pads an utterance with does not lower it.
FRAME_S = 0.02
ACTIVE_THRESHOLD_DBFS = -40.0
DEFAULT_TARGET_DBFS = -22.0

# Three sentences of different shapes (a statement, a question, a description),
# about 15 s of speech: enough for a stable level, cheap for a hosted voice.
DEFAULT_TEXTS = (
    (
        "Hello! I'm the voice you'll be hearing from now on, so let me say a few"
        " words to get a feel for my level."
    ),
    (
        "Could you tell me what you would like to do today, and whether there is"
        " anything I should know first?"
    ),
    (
        "The weather looks clear this afternoon, with a light breeze from the west"
        " and temperatures around twenty degrees."
    ),
)

_FULL_SCALE = 32768.0


@dataclass(frozen=True)
class Level:
    """The level of some speech, in dBFS rounded to 0.1 dB."""

    level_dbfs: float | None  # RMS of the active speech; None when none is active
    peak_dbfs: float | None  # largest absolute sample; None for a silent signal


@dataclass(frozen=True)
class GainSuggestion:
    """The engine `gain_db` that brings a level to `target_dbfs`."""

    target_dbfs: float
    gain_db: float  # within the field's range, to 0.1 dB
    peak_after_dbfs: float  # the peak once `gain_db` is applied
    limited: bool = False  # the field's range stopped `gain_db` short of the target

    @property
    def clips(self) -> bool:
        """Whether that gain pushes the peak past full scale."""
        return self.peak_after_dbfs > 0


@dataclass(frozen=True)
class Calibration:
    """What `calibrate` measured, and the gain it suggests."""

    level: Level
    suggestion: GainSuggestion
    duration_s: float  # the audio measured, silences included


def _dbfs(amplitude: float) -> float:
    # + 0.0 turns a rounded -0.0 (a full-scale peak) into 0.0.
    return round(20 * math.log10(amplitude / _FULL_SCALE), 1) + 0.0


def _check_target(target_dbfs: float) -> None:
    if not math.isfinite(target_dbfs) or not (-60 <= target_dbfs <= 0):
        raise ValueError(f"target_dbfs must be from -60 to 0, got {target_dbfs!r}")


def measure_level(pcm: bytes, sample_rate: int) -> Level:
    """The active level and the peak of signed 16-bit little-endian mono PCM.

    The PCM is cut into 20 ms frames; the frames whose own RMS is above
    -40 dBFS are the active speech, and the level is the RMS of all their
    samples. A trailing partial frame is left out of the level, not the peak.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be > 0, got {sample_rate}")
    if len(pcm) % 2:
        raise ValueError(f"PCM of {len(pcm)} bytes is not whole 16-bit samples")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    frame = max(1, round(FRAME_S * sample_rate))
    frames = samples[: samples.size // frame * frame].reshape(-1, frame)
    threshold = (_FULL_SCALE * 10 ** (ACTIVE_THRESHOLD_DBFS / 20)) ** 2
    active = frames[(frames**2).mean(axis=1) > threshold]
    return Level(
        level_dbfs=_dbfs(math.sqrt(float((active**2).mean()))) if active.size else None,
        peak_dbfs=_dbfs(peak) if peak else None,
    )


def suggest_gain(
    level: Level, target_dbfs: float = DEFAULT_TARGET_DBFS
) -> GainSuggestion:
    """The `gain_db` that brings `level` to `target_dbfs` (-60 to 0), clamped
    to the field's range. Raises ValueError when there is no active speech."""
    _check_target(target_dbfs)
    if level.level_dbfs is None or level.peak_dbfs is None:
        raise ValueError("no active speech to measure a level from")
    wanted = round(target_dbfs - level.level_dbfs, 1)
    gain = min(max(wanted, GAIN_DB_MIN), GAIN_DB_MAX)
    return GainSuggestion(
        target_dbfs=target_dbfs,
        gain_db=gain,
        peak_after_dbfs=round(level.peak_dbfs + gain, 1) + 0.0,
        limited=gain != wanted,
    )


class _CaptureSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def drain(self) -> None:
        pass


async def calibrate(
    config: TTSEngineConfig,
    *,
    target_dbfs: float = DEFAULT_TARGET_DBFS,
    texts: Sequence[str] | None = None,
) -> Calibration:
    """Say `texts` (default: `DEFAULT_TEXTS`) through an engine built from
    `config`, measure the level of all their audio together, and suggest the
    `gain_db` for `target_dbfs`.

    The module's own level is measured: a `gain_db` already in `config` is
    ignored, so the suggestion replaces it. One `say` per text, nothing played
    (a capture sink), the engine closed before returning. Raises what the
    engine raises (ConfigError, TTSError), and ValueError for no text, a bad
    target or a module that produced no active speech.
    """
    texts = DEFAULT_TEXTS if texts is None else tuple(texts)
    if not texts or not all(t.strip() for t in texts):
        raise ValueError("calibrate needs at least one non-empty text")
    _check_target(target_dbfs)
    sink = _CaptureSink()
    async with TTSEngine(replace(config, gain_db=0.0), sink=sink) as engine:
        for text in texts:
            await engine.say(text)
        sample_rate = engine.sample_rate
    pcm = b"".join(sink.chunks)
    level = measure_level(pcm, sample_rate)
    return Calibration(
        level=level,
        suggestion=suggest_gain(level, target_dbfs),
        duration_s=round(len(pcm) / 2 / sample_rate, 2),
    )
