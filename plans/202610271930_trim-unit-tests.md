# Trim the unit tier

**Status:** Done

Implements `specs/testing.md` ("Smell checklist", "Shared helpers are tested once", "No settle sleeps, no real poll intervals", "Speed budget"), following the audit in `analysis/unit-test-audit.md`. The fast tier had grown to 795 cases in 6.3 s against a 5 s budget: per-module copies of tests for the shared `base.py` helpers (left behind when PR #7 moved the code), near-copies added by review-fix commits, and fixed sleeps. This plan removes the redundant cases, merges and parametrizes the near-identical ones, and replaces sleeps with events or patched poll constants. It changes no library code and no `tests-e2e/` test; it removes no behavioural coverage, and closes one coverage gap (`test_valid_config` used the default server values).

## Scope

- `tests/modules/test_{elevenlabs,gradium,mistral,deepgram,gemini,cartesia,openai}.py` — API-key tests reduced to the one wiring test; helper-variant validation rows dropped; carry and slicing tests merged; copied cleanup/cancel/incremental tests deleted or merged; poll constants patched in the fixtures; cancel settle sleeps removed; the per-file near-copies listed in the audit removed.
- `tests/modules/test_{pocket,phonon,qwen3,kokoro,piper,tone,audiofile}.py` — validation functions folded into `(config, match)` tables without the helper variants; tests re-testing `feed_with_lookahead`, `resolve_path` and the worker helpers deleted; missing-extra tests deleted (covered by `test_lazy_imports.py`); per-sentence and incremental stream tests merged; `test_close_drops_*` observe the fake runtime through a `weakref` instead of `vars(module)`; cancel settle sleeps removed.
- `tests/modules/test_tts_module_interface.py` — two non-independent tests removed, the long-word tests merged, fixed waits shortened.
- `tests/test_engine.py`, `tests/test_config.py`, `tests/test_audio.py`, `tests/test_mcp.py` — the near-copies the audit lists removed or merged; `test_valid_config` uses non-default server values.
- `specs/testing.md` — the "Shared helpers are tested once" and "No settle sleeps" rules.

## Steps

1. Speed, with no assertion removed: patch `_POLL_SECONDS` / `_RECV_POLL_SECONDS` in the cartesia, deepgram, elevenlabs and gradium fixtures; scale cartesia's rotation pauses to them; drop the post-cancel settle sleeps (keeping each module's own cancel assertions, under `asyncio.wait_for`); replace the interface tests' fixed waits with events.
2. Helper overlap (spec, "Shared helpers are tested once"): per provider keep `test_api_key_from_env` and drop the other API-key tests and rows; keep one validation row per field and per bound; merge each carry test with that module's slicing test; drop clipping-value asserts the `float_to_int16_pcm` test owns.
3. Copied blocks and near-copies: apply the audit's §3–4 file by file, running that file's tests after each.
4. Smells: the `weakref` close tests; drop the tests that patch or pin the implementation (`patch.object(TTSTools, "say")`, the keyword-callback asserts); fix `test_valid_config`.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` all pass, and the unit tier runs in under 5 s. `git diff --stat main -- src tests-e2e` is empty: no library code or live test changed. Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.

## Result

795 → 584 cases; the unit tier runs in about 2.2 s (from 6.3 s). Three audit items were deliberately not applied as written, because reading the code showed they guard a distinct path: gradium's `test_partial_voice_settings_send_only_what_was_set` and gemini's `test_language_alone_sends_no_voice_config` (one optional setting alone — neither the all-defaults nor the all-set test reaches that branch) are kept; elevenlabs' text-stream callback test is kept without its failing-close half (no other test covers callback propagation on that path). `base._POLL_SECONDS` is patched down in the interface tests under the same rule as the module poll constants.
