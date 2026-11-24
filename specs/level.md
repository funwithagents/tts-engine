---
code:
  - src/tts_engine/level.py
  - src/tts_engine/level_cli.py
  - pyproject.toml
tests:
  - tests/test_level.py
  - tests/test_level_cli.py
---

# Level and gain calibration

**Status:** Implemented

## Purpose

The engine block's `gain_db` ([configuration.md](configuration.md), "`engine.gain_db`") is only useful if its value is easy to find. A voice's level is a property of its whole module configuration and stable across texts, so finding the value means measuring that configuration's level once and taking the difference to a target level.

`tts_engine.level` does that, in the library, so that any consumer of the installed package can calibrate its own configurations, without a checkout of this repository:
- a pure measure over PCM;
- the gain suggestion that follows from it;
- `calibrate`, which renders texts through an engine and does both;
- the `tts-engine-level` command over `calibrate`.

The voice audition tool ([voice-audition.md](voice-audition.md), "Level and gain") uses the same measure on the samples it already renders, so a level read on its page and one printed by the command are the same number.

## Core concepts / Decided

### The measure: `measure_level(pcm, sample_rate) -> Level`

```python
@dataclass(frozen=True)
class Level:
    level_dbfs: float | None  # RMS of the active speech; None when none is active
    peak_dbfs: float | None   # largest absolute sample; None for a silent signal
```

- **Input:** signed 16-bit little-endian mono PCM, the engine's sink format ([audio-sink.md](audio-sink.md)), at `sample_rate`. An odd byte count or a `sample_rate` ≤ 0 raises `ValueError`.
- **Active speech:** the PCM is cut into 20 ms frames (`FRAME_S`), and the frames whose own RMS is above −40 dBFS (`ACTIVE_THRESHOLD_DBFS`) are kept. A trailing partial frame is left out of the level, not out of the peak.
- **`level_dbfs`** is the RMS of all the kept frames' samples. Leaving the silences out matters: engines pad an utterance with up to 0.7 s of silence, which lowers a whole-signal RMS without changing what is heard.
- **`peak_dbfs`** is the largest absolute sample.
- **dBFS** is relative to 32768, and both values are rounded to 0.1 dB.
- **Pure:** no I/O, no engine, no state. It is the function the audition calls on its captured audio, so it never pays for a second synthesis.

Active RMS matched the ear on the voices it was first tried on (ten modules on one robot speaker). Perceptual loudness (ITU-R BS.1770, LUFS) is stricter (Open questions).

### The suggestion: `suggest_gain(level, target_dbfs=-22.0) -> GainSuggestion`

```python
@dataclass(frozen=True)
class GainSuggestion:
    target_dbfs: float
    gain_db: float          # within the field's range, to 0.1 dB
    peak_after_dbfs: float  # the peak once gain_db is applied
    limited: bool = False   # the field's range stopped gain_db short of the target

    @property
    def clips(self) -> bool: ...  # peak_after_dbfs > 0
```

- `gain_db` is `target_dbfs − level_dbfs`, rounded to 0.1 dB, then clamped to the field's range (`GAIN_DB_MIN`…`GAIN_DB_MAX` in `config.py`, −60 to +24). A suggestion is always a valid config value; `limited` says when the clamp moved it.
- `peak_after_dbfs` is `peak_dbfs + gain_db`. `clips` is true when it passes 0 dBFS: the engine would clip there, and warn ([architecture.md](architecture.md), "Output gain").
- **Errors:** `target_dbfs` must be finite and from −60 to 0, and the level must have active speech; anything else raises `ValueError`.
- **Default target: −22 dBFS** (`DEFAULT_TARGET_DBFS`). It is below most voices measured, so most suggestions are negative, and attenuating never clips: on the ten voices first measured, −22 gives −7.6 to +0.7 dB with every peak at or below −1.7 dBFS.

### Rendering and measuring: `await calibrate(config, *, target_dbfs=-22.0, texts=None) -> Calibration`

```python
@dataclass(frozen=True)
class Calibration:
    level: Level
    suggestion: GainSuggestion
    duration_s: float  # the audio measured, silences included
```

1. **Validate before synthesizing.** `texts` (default `DEFAULT_TEXTS`) must be at least one text, none blank, and the target must be valid (`ValueError` otherwise). Nothing is synthesized for a bad call.
2. **Build the engine with no gain.** The engine is built from `dataclasses.replace(config, gain_db=0.0)` with a capture sink. The module's own level is measured, so the suggestion is an absolute value to put in the config. It replaces any `gain_db` already there, rather than adding to it. Nothing is played, and no sounddevice is imported.
3. **Say and measure.** Each text is said once, in order, with no warm-up (the level does not depend on one). All their audio is measured together, as one signal, so the level covers every text.
4. **Close.** The engine is closed before returning (`async with`), on every path.

Construction and synthesis errors propagate as the engine raises them (`ConfigError`, `TTSError`). A module that produced no active speech raises `ValueError` from `suggest_gain`.

**`DEFAULT_TEXTS`**: three sentences of different shapes (a greeting, a question, a description), about 320 characters and 15 s of speech. One sentence gives a usable level; three give a firmer one at a small cost for a hosted voice.

`tts_engine.level` is the public import path. Its names are not re-exported from the package top level, which stays the engine's surface ([architecture.md](architecture.md), "Public API").

### The command: `tts-engine-level`

Declared in `[project.scripts]` as `tts-engine-level = "tts_engine.level_cli:main"`, installed with the base package, and needing no extra beyond the configured module's own.

```bash
tts-engine-level --config engine.json [--target -22] [--text TEXT …] [--json] [--log-level LEVEL]
```

- **`--config`** is an engine block, or a whole MCP-server config whose `engine` block it takes: a top-level object with an `engine` key is read as the latter. Relative paths resolve against the file's directory, as `from_json_file` does. Only the engine block is validated.
- **`--text`** (repeatable) replaces the built-in texts.
- **Before synthesizing**, it prints to stderr how many texts and characters it is about to synthesize, with which module: a hosted module makes paid calls.
- **Output** (stdout):
  - by default, one aligned line each for the measured duration, the level, the peak, the target, the suggested `gain_db` and the peak after it, plus the config's current `gain_db` when it has one (marked as ignored while measuring);
  - with `--json`, one object: `module`, `duration_s`, `level_dbfs`, `peak_dbfs`, `target_dbfs`, `gain_db`, `peak_after_dbfs`, `limited`, `clips`, `current_gain_db`.
- **Warnings** (stderr) when the suggestion is `limited` or `clips`.
- **It prints the value, it does not write it.** Rewriting the user's JSON would lose its formatting and key order. Copying one number into the engine block is the calibration.
- **Exit codes:**
  - `0` on a result, warnings included;
  - `2` for an unreadable or invalid config, a bad target or text, or no active speech to measure;
  - `1` for a synthesis failure (`TTSError`).
- **Logging:** it is an entry point, so it configures logging itself, `logging.basicConfig` with `--log-level` (default `WARNING`), like `tts-engine-mcp` ([project.md](project.md), "Logging").

## Open questions

Deferrals only:

1. **LUFS instead of active RMS**, if the RMS and the ear disagree on a set of voices. `measure_level` would keep its shape; the value would change, so it would be a deliberate re-calibration.
2. **Writing the value** (`--write`) into the config file, if copying it by hand proves tedious. It needs a JSON rewrite that keeps the file's layout, or a documented reformat.
3. **Several configurations at once** (one calibration per entry of a list), for a consumer with many voices. The audition covers that case today, since it measures every candidate it renders.
