# Unit tier cleanup

**Status:** Done

Applies `specs/testing.md` ("What a good test asserts", "Smell checklist", "Shared helpers are tested once", "No settle sleeps, no real poll intervals") to the whole unit tier, after a review of every test file on 2026-11-17. Tests that another test already covers are deleted or merged, tests that assert private state or wait on real time are rewritten, and the module specs' *Testing* sections follow. Missing coverage the review found is a separate plan.

## Scope

- `tests/modules/test_*.py` for deepgram, cartesia, elevenlabs, gradium, openai, piper, kokoro, phonon, tone, audiofile, qwen3, mistral, gemini, openai-speech-compatible
- `tests/test_engine.py`, `tests/test_config.py`, `tests/test_extras.py`, `tests/test_benchmark.py`, `tests/audition/test_audition.py`
- The *Testing* sections of the matching `specs/modules/*.md` (editorial; no status changes)

## Steps

1. **Covered elsewhere: delete or merge.**
   - The `test_missing_extra_raises_config_error` of seven provider files: `tests/modules/test_lazy_imports.py` checks every provider's missing-extra `ConfigError` and install hint. OpenAI's own test stays, for its `websockets` row.
   - The `…feeds_audio_before_the_source_finishes` tests of elevenlabs and gradium: the idle-deadline pause tests have the same source and assertions.
   - Deepgram's construction-connects test, Cartesia's silent-server test and its cancel test merged into the cancelled-tail test, OpenAI's construction test merged into its session-options test, openai-speech-compatible's unchanged-chunks test merged into its slicing test.
   - The text-stream cancel tests of piper, kokoro and phonon: the shared runner's cancel matrix.
   - Smaller merges in `test_engine.py` and `test_config.py`.
   - `test_extras.py`'s test of its own helper.
2. **Shared-helper rows.** The `threads` bool/string/float rows (piper, kokoro, phonon) and OpenAI's `speed`/`timeout` bool/NaN rows re-test `positive_int` and `finite_number`; the module's own bounds stay. Qwen3's failing-close variant becomes the plain callback-error test, as the other HTTP modules have.
3. **Private state.** Deepgram, Cartesia and OpenAI tests assert observable effects (the next `say` reconnects or not, the connect count) instead of `_socket` attributes. Setup may still age a held socket's stamps. OpenAI ages its socket by patching `_ROTATE_AFTER`. `test_benchmark.py` reads the statistics from `results.csv`, not `_stats`; `test_audition.py` reads the template as package data.
4. **Real time.**
   - OpenAI's fixture patches `_POLL_SECONDS` down, as the other polling modules' fixtures do.
   - The settle sleeps after a cancel (deepgram, cartesia) are gone; Cartesia joins its sender thread instead.
   - The tone, audiofile, piper and kokoro cancel tests hold the worker in a gated callback instead of pacing it with sleeps.
   - The CPU sampler test runs against a scripted clock and event.
5. **Kept on purpose**: elevenlabs' v4 complete-text test (a spec decision a refactor could break), the engine's failing-drain-alone test (a sink-spec guarantee), cartesia's reconnect handshake timeout (a distinct call site), deepgram's send-after-stop test (the race it guards is not reachable through the public API), the validation rows that document accepted formats.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` (994 passed, from 1021; five runs, 4.1 to 5.0 s).
- The rewritten cancel tests were run 30 times in a row.
