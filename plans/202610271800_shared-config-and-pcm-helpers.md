# Shared config and PCM helpers

**Status:** Done

Implements the settled behavior in `specs/tts-module-interface.md` ("Shared config helpers", "Shared PCM helpers") and `specs/configuration.md` ("Constructors": the loaders read UTF-8). This is point 2 of the repository review (`analysis/repo-review.md`, "Suggested order of work"): the small shared helpers, each a mechanical refactor with the existing tests as the net, so a validation fix can no longer land in one provider and miss another. It deliberately leaves out the review's two other code items (explicit types for the session and queue messages; telling a missing extra from an incompatible installed API) and the documentation restructuring (point 3).

## Scope

- `src/tts_engine/modules/base.py` — the new helpers: `resolve_api_key`, `optional_str`, `finite_number`, `float_to_int16_pcm`, `slice_bytes_for`, `PCMFeeder`.
- `src/tts_engine/modules/{elevenlabs,gradium,mistral,deepgram,gemini,cartesia,openai}/module.py` — the seven `_resolve_api_key` copies go; `timeout` and the other optional strings and numbers read through the shared validators.
- `src/tts_engine/modules/{pocket,phonon,qwen3,kokoro,piper,tone}/module.py` — local validators replaced; the four `_to_int16_pcm` copies replaced by `float_to_int16_pcm` (pocket keeps its tensor-to-CPU step); kokoro and piper take their slice size from `slice_bytes_for`.
- `src/tts_engine/modules/{openai,mistral,gemini,deepgram,cartesia}/module.py` — the odd-byte carry and the slice loop replaced by one `PCMFeeder` per utterance; gemini thereby feeds in 100 ms slices and drops its chunk-size assumption.
- `src/tts_engine/config.py` — both `from_json_file` loaders read the file as UTF-8 whatever the platform's default encoding; a file that is not UTF-8 is a `ConfigError` naming the path.
- `tests/modules/test_tts_module_interface.py` — behavioural tests for each helper.
- `tests/test_config.py` — a non-ASCII config loads; a non-UTF-8 file is a `ConfigError`.
- `tests/modules/test_gemini.py` — a large part is fed in 100 ms slices.
- `specs/tts-module-interface.md`, `specs/configuration.md`, `specs/modules/*.md` — the helpers specced once; the per-module specs point at them instead of describing their own copy.

## Steps

1. Add the helpers to `base.py` with the exact messages the modules produce today (`"<label> 'api_key' must be a string"`, `"<label> '<field>' must be a non-empty string"`, `"<label> '<field>' must be a finite number > 0"`, …), so the per-module tests that match on a field name keep passing unchanged. `optional_str` and `finite_number` keep the two policies the modules chose: an absent field takes the default, an explicit `null` with a default is rejected like any other non-string, and with no default `null` is absent.
2. Replace each module's private copy with the shared helper, one module at a time, running that module's tests after each.
3. Replace the carry/slice loops of openai, mistral, gemini, deepgram and cartesia with `PCMFeeder`; mistral aligns on 4-byte float32 samples and converts through the feeder's `convert` hook.
4. UTF-8 in `config.py`; the loader tests.
5. Tests for the helpers; the gemini slicing test.
6. Specs: one section per helper group in the interface spec; the per-module specs reference them; `configuration.md` states the encoding rule; `gemini.md` drops the chunk-size assumption.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` all pass; the live tier is not needed (no provider behaviour changes except gemini's slicing, which the unit test covers). Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
