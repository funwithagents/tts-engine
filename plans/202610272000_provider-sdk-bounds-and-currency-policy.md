# Provider SDK bounds and the API-currency policy

**Status:** Done

Implements `specs/project.md` ("Dependency strategy for TTS backends": the upper-bound rule and the follow-the-current-API rule), `specs/modules/elevenlabs.md` ("Dependencies and lazy import") and `specs/modules/phonon.md` ("Dependencies and lazy import", "Validated against `ptts`"), following items 1, 2 and 8 of `analysis/api-currency-audit.md`. It refreshes the two lagging SDK pins and writes down the rule the later audit items apply. It leaves out the module work those items lead to: the `openai` → `openai-compatible` rename (its own plan, `202610272010`), Deepgram Flux, the OpenAI Realtime module and the ElevenLabs v3/v4 models.

## Scope

- `pyproject.toml` — `elevenlabs>=2.39,<3` (was unbounded) with its reason; `ptts>=0.4.2,<0.6` (was `<0.5`) with "validated on 0.5.0".
- `uv.lock` — `elevenlabs` 2.39.0 → 2.74.1, `ptts` 0.4.2 → 0.5.0.
- `specs/project.md` — the extras table shows both bounds; two new bullets in "Dependency strategy for TTS backends": every provider extra carries an upper bound with a reason, and a module follows the provider's current API (OpenAI and Deepgram as the worked cases). Editorial; status unchanged.
- `specs/modules/elevenlabs.md` — the declared extra and the reason for `<3`. Editorial.
- `specs/modules/phonon.md` — the declared bound, the 0.5.0 re-validation and why its release-note changes do not reach the module. Editorial.

## Steps

1. `uv lock --upgrade-package elevenlabs --upgrade-package ptts`, `uv sync --dev`.
2. Check the ElevenLabs module against 2.74.1: `text_to_speech.stream` still takes every parameter the module passes (the audit verified this in a scratch environment); the stream-input socket is driven by `websockets` directly, so it does not depend on the SDK. Run the module's unit tests with `-W error::DeprecationWarning`: no new import-time warning, so `filterwarnings` is unchanged.
3. Check the Phonon module against 0.5.0, point by point against the spec's validation list: the `TTS` constructor keywords, `add_voice`, `stream(text, voice=)`, `sample_rate` / `device` / `quant`, the error types for each rejected value, 1920-sample chunks, warm time to first chunk, close time, use from another thread. `DEFAULT_MODEL` and `DEFAULT_VOICE_CATALOG` keep their 0.4.2 revisions. The release's catalog change does not apply: the module adds no catalog, only a single voice file.
4. Bound the elevenlabs extra and move the ptts bound, each with its comment; `uv lock`.
5. Spec edits listed in Scope.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest`; live `-k "elevenlabs or phonon"` via `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k ...'`.

## Result

No library code changed. Both modules pass their unit tests and their live rows on the new versions (with `openai`, 15 live cases pass). On 0.5.0, Phonon's warm time to first chunk is ~9 ms on CPU and a mid-generation close takes ~5 ms (it was ~2 ms on 0.4.2).
