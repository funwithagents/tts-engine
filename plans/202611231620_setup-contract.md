# Setup: fields declared once, the `setup` module

**Status:** In progress

Implements `specs/setup.md`: module fields declared as data and read by both the constructor and `validate`, and `tts_engine.setup` with its seven functions. In two parts, so the mechanism is proven on three modules before the other twelve are converted. It leaves out the demo app (a separate spec).

## Scope

- `src/tts_engine/modules/fields.py` — new: `Field`, `read_fields`, `field_problems`, `fields_schema`
- `src/tts_engine/modules/base.py` — `TTSModule` gains `title`, `extra`, `fields`, `problems`, `items`, `prepare`; the shared helpers become wrappers over `read_fields`
- `src/tts_engine/modules/<name>/module.py` — each module declares its fields and reads them before any import
- `src/tts_engine/setup.py` — new: `describe`, `requirements`, `validate`, `check`, `prepare`, `options`, `probe`
- `src/tts_engine/config.py` — the engine-level checks callable without raising, for `validate`
- `tests/modules/test_fields.py`, `tests/test_setup.py` — new
- `tests/test_extras.py` — compares each module's `extra` with `pyproject.toml` instead of deriving it by pattern
- `pyproject.toml` — `module-setup` in the `dev` group, for the conformance check
- `specs/setup.md`, `specs/tts-module-interface.md`, `specs/project.md`, `AGENTS.md` — frontmatter, the new class members, the dev dependency, the project map rows
- `docs/` — a page on setting the engine up from an application

## Steps

### Part 1 — the mechanism, on three modules (done)

1. `fields.py`: the three functions, with the rules and messages of today's shared helpers. Tests first, from the helpers' existing tests.
2. `TTSModule`: the new class members and their defaults. `optional_str`, `finite_number`, `positive_int` rewritten over `read_fields`, their tests unchanged.
3. Convert `tone` (a fixture: no extra, no item), `elevenlabs` (hosted: key fields, a required field) and `kokoro` (local: weights in the Hugging Face cache, so `items` and `prepare` are real). Each constructor starts with `read_fields` and its cross-field rule, before its imports. Their existing tests must pass unchanged: same messages.
4. `setup.py` over the registry, tolerating modules not converted yet (no branch, `validate` skips their fields), so the suite stays green between the two parts.
5. `probe`: build the engine, speak the test sentence, close, report; over `tone` and a recording sink in the tests.
6. `tests/test_setup.py`, including the conformance check and the subprocess test that `validate` and `requirements` import no provider library.

Done on 2026-11-23, with `options` (voices, output devices) brought forward from part 2 since it needed nothing more. `module-setup` is a dev dependency resolved from the branch of its first pull request, to be moved to `main` once that is merged.

Review point: the contract and `Field` are adjusted here if the three modules show a gap, before the rest is converted.

### Part 2 — the other twelve modules (done, but for step 11)

7. Convert the hosted modules: `gradium`, `mistral`, `deepgram`, `gemini`, `cartesia`, `openai`, `openai-speech-compatible`.
8. Convert the local ones: `piper` (Hugging Face cache, like `kokoro`), then `pocket`, `phonon`, `qwen3`, establishing for each whether its cache can be read without loading the model (`unknown` otherwise).
9. Convert `audiofile`.
10. Remove the tolerance of step 4: every registered module must declare its fields. Remove the shared helpers no module calls any more.
11. Point the `module-setup` source at `main` (waits for that project's pull request to be merged).

Done on 2026-11-23. What the twelve modules added to the design: a field of several kinds (piper's `speaker`), a field that refuses `null` without a default (pocket's `voice`), a list-of-objects field (audiofile's `files`), and `fields_for(config)` for rules that follow another field (deepgram). Step 10's removal of the shared helpers stops short: `list_voices` still calls them. `phonon`'s items are `unknown`, for the reason given in the spec.
12. Specs, project map and docs; `specs/setup.md` to `Implemented`.

## Verification

- `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`: all pass, with the existing module tests unmodified except where they call a removed helper.
- Every config under `examples/` passes `validate`.
- The live tier (`uv run pytest tests-e2e -rs`) for the modules whose keys are available: a converted module still speaks.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
