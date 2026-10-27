# Validation fixes and documentation drift

**Status:** Done

Implements the settled validation rules in [pocket.md](../specs/modules/pocket.md), [phonon.md](../specs/modules/phonon.md), [qwen3.md](../specs/modules/qwen3.md) (finite numbers), [audiofile.md](../specs/modules/audiofile.md) (positive rate, truncated payload) and [gemini.md](../specs/modules/gemini.md) (mono mime), and repairs the documentation drift listed by the 2026-10-26 module code review. Closes findings F9, F10 and F11 and the review's "specific documentation drift" table. Leaves out the review's restructuring suggestions (shorter README, a `docs/` tree, shared credential/validation helpers), which are design work of their own.

## Scope

- `src/tts_engine/modules/{pocket,phonon,qwen3}/module.py` — `math.isfinite` on `temperature` (and phonon's `cfg_coef`).
- `src/tts_engine/modules/audiofile/module.py` — `framerate <= 0` is a `ConfigError`; at playback an odd-length chunk, or an end before the declared frame count, is a `TTSError`.
- `src/tts_engine/modules/gemini/module.py` — `_check_mime` parses the parameters and rejects `channels` other than 1 (omitted parameters take their documented values).
- `tests/modules/test_{pocket,phonon,qwen3,audiofile,gemini}.py` — `NaN`/infinity rows; a zero-rate header; a payload missing one byte (nothing fed) and one missing whole frames (its frames fed, then the error); `channels=2` rejected and a full mono mime accepted.
- `README.md` — provider installs are `uv sync --no-dev --extra <name>` (a bare `--extra` materializes the whole dev environment); the format check is the code-directory one CI runs.
- `specs/project.md` — the format command bullet; the `specs/` folder wording; the dangling `_http-server.md` link.
- `specs/modules/openai.md` — no link into the untracked `analysis/` folder.
- `plans/202610261930_runtime-contract-fixes.md` — the ElevenLabs empty-input claim matches what was built (the socket is opened; nothing is sent).
- `specs/tts-module-interface.md` — the lookahead section's "users" sentence says which helper it means.
- `examples/config.audiofile.json` — points at the committed `tests-e2e/fixtures/*.wav` (checked: the example loads and plays through a capture sink).
- `specs/_todo.md` — only the candidate modules not built yet.
- `AGENTS.md` — `specs/` described as the living contract, not "pre-implementation".

## Steps

1. The three finite-number checks and their parametrized rows.
2. The audiofile checks (construction and playback) and their three tests, generating the corrupt files from a valid one.
3. The gemini mime parameters and the two tests.
4. The documentation edits, each a one-line fix at the location the review named.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` (including `tests/test_project_map.py`, which checks the spec frontmatter and the map) — green; the live `audiofile` rows passed.
