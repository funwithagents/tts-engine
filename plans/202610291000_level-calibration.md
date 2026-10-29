# Level calibration: `tts_engine.level` and `tts-engine-level`

**Status:** Done

Implements [specs/level.md](../specs/level.md). It moves the level measure out of the voice audition and into the library, so a consumer of the installed package can find a configuration's `gain_db` with no checkout: `measure_level` over PCM, `suggest_gain` to a target, `calibrate` through an engine, and the `tts-engine-level` command over it. The audition imports the measure instead of keeping its own. It deliberately leaves out writing the value into a config file, LUFS, calibrating several configurations in one call, and packaging the audition itself (next, as its own change).

## Scope

- `src/tts_engine/level.py` — `Level`, `GainSuggestion` (`clips`, `limited`), `Calibration`; `measure_level`, `suggest_gain`, `calibrate`; `DEFAULT_TARGET_DBFS`, `DEFAULT_TEXTS`.
- `src/tts_engine/level_cli.py` — `main(argv=None) -> int`: engine block or MCP config, `--target`, `--text`, `--json`, `--log-level`, the stderr notice and warnings, exit codes 0/1/2.
- `pyproject.toml` — the `tts-engine-level` script.
- `tests/test_level.py`, `tests/test_level_cli.py` — the measure on known sines, the suggestion and its clamp, `calibrate` on the `tone` fixture (native level, every text said, bad input before synthesis); the command's outputs, config forms, warnings and exit codes.
- `tools/voice-audition/audition.py`, `tests/test_voice_audition.py` — the measure imported from `tts_engine.level`, its test moved to `tests/test_level.py`.
- `tests/test_docs_examples.py` — the calibration example of `docs/configuration.md` executed.
- `specs/level.md` (new), `specs/_index.md`, `specs/project.md`, `specs/architecture.md`, `specs/configuration.md`, `specs/voice-audition.md`, `AGENTS.md` — the spec, the second entry point and logging, the map rows and command.
- `docs/configuration.md`, `docs/choosing-a-backend-and-voice.md`, `tools/voice-audition/README.md` — how to calibrate.

## Steps

1. `level.py` with its tests.
2. `level_cli.py`, the script entry, its tests.
3. The audition on the library measure.
4. Specs, the AGENTS.md map, user docs and their executed example.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks tools`, `uv run pyright`, `uv run pytest` clean. `uv run tts-engine-level --config examples/config.tone.json` prints −17.0 dBFS and `gain_db −5.0`. On piper `en_US-ryan-medium` it gives about −15.5 dBFS, within about 1 dB of the analysis's −14.4, which was measured on other texts after resampling to 16 kHz.
