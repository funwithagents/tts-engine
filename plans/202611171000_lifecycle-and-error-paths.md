# Lifecycle and error paths

**Status:** Done

Closes four gaps between the specs and the code found by the repository consistency audit of 2026-11-16, each on a lifecycle or error path the tests did not reach: a text source left open by a `say_stream` that never starts, an audition pass stopped by a failing close, configs built in Python skipping validation, and Phonon's thread warning missing after a default-sized first model. Timeouts, the smaller drift items and the documentation are separate plans.

## Scope

- `src/tts_engine/engine.py` — `say_stream` closes its source when it is cancelled while queued or finds the engine closed
- `src/tts_engine/config.py` — `PlayerConfig`, `ServerConfig` and `TTSEngineConfig` validate in `__post_init__` with the checks `from_dict` uses
- `src/tts_engine/audition/audition.py` — `render_candidate` records a failing `engine.close()` instead of raising it
- `src/tts_engine/modules/phonon/module.py` — a `_pool_sized` flag beside `_pool_threads`
- `tests/test_engine.py`, `tests/test_config.py`, `tests/audition/test_audition.py`, `tests/modules/test_phonon.py` — one functional test per path
- `specs/architecture.md`, `specs/tts-module-interface.md`, `specs/configuration.md`, `specs/voice-audition.md`, `specs/modules/phonon.md`, `docs/configuration.md` — the rules stated

## Steps

1. **Text source.** In `say_stream`, acquire the lock explicitly: a `BaseException` while waiting closes the source (`_aclose(..., failed=True)`) and propagates. `_check_open()` moves inside the block that closes the source. The sink is drained only around the synthesis, so a call that was never admitted does not drain.
2. **Config.** Move the module, player, server and gain checks into `_check_module`, `_check_player`, `_check_server` and `_check_gain_db`; call them from `from_dict` (same messages, same order) and from each dataclass's `__post_init__`. `TTSEngineConfig.__post_init__` also requires a `PlayerConfig` and stores `gain_db` as a float.
3. **Audition.** `render_candidate` keeps the candidate's error in a variable, closes the engine in its own `try`, and applies the `cleanup` precedence: a close failure is the error when nothing else failed, logged otherwise.
4. **Phonon.** `_pool_sized` records that a model was loaded; the warning tests it instead of `_pool_threads is not None`.

## Verification

- `test_say_stream_closes_a_source_cancelled_while_queued`, `test_say_stream_closes_its_source_on_a_closed_engine`
- `test_direct_construction_is_validated_like_the_loaders`, `test_direct_construction_stores_the_gain_as_a_float`
- `test_a_failing_close_is_recorded_and_the_pass_goes_on`
- `test_a_count_after_a_default_first_model_warns`
- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest`
