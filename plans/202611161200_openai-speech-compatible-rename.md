# Rename `openai-compatible` → `openai-speech-compatible`

**Status:** Done

Implements the naming rule for protocol modules adopted on 2026-11-16 ([specs/tts-module-interface.md](../specs/tts-module-interface.md), "Module names and renamed types"; [specs/project.md](../specs/project.md), "Dependency strategy for TTS backends"): a protocol module is named `<origin>-<api>-compatible`, after the API it implements. "OpenAI-compatible" alone does not say which API, since OpenAI produces speech through `POST /v1/audio/speech` (this module), the Realtime API (the `openai` module) and Chat Completions with audio output. So the `/v1/audio/speech` module becomes `openai-speech-compatible`. The old name keeps working through a table of renamed types that logs a deprecation warning. The extra keeps its name and content: the one `openai-compatible` extra holds the dependencies of every OpenAI-protocol module. There is no behaviour change. [202610272010_openai-compatible-rename.md](202610272010_openai-compatible-rename.md) is the template.

Out of scope: an `openai-realtime-compatible` module (it would join the `openai-compatible` extra, which would then pull `openai[realtime]`), any change to the vendor module `openai`, and any change to `pyproject.toml` extras.

## Scope

- `src/tts_engine/modules/openai_compatible/` → `openai_speech_compatible/`: `OpenAICompatibleModule` → `OpenAISpeechCompatibleModule`, and the labels and `TTSError` prefix become `OpenAI speech-compatible …`. Two strings stay. The install hint `tts-engine[openai-compatible]` names the extra, which keeps its name. The WAV error `OpenAI-compatible server returned WAV` describes the server, not the module.
- `src/tts_engine/modules/__init__.py`: `REGISTRY["openai-speech-compatible"]`. Also `RENAMED = {"openai-compatible": "openai-speech-compatible"}` and `resolve_type(name)`, which follows `RENAMED` with a `log.warning` naming the new type, then raises `ConfigError` for an unknown one. `load_module` and `list_voices` look a type up through it.
- Other places that look a `type` up: `benchmarks/bench.py` (`module_kind`, through `resolve_type`, so older records still resolve; `REQUIRED_IMPORT`) and `src/tts_engine/audition/audition.py` (`parse_module`, which canonicalizes the name). The MCP server and `tts-engine-level` only log the configured type and build the engine through `load_module`, so they need no change.
- `tests/test_extras.py`: a provider's extra is its `type`, except that a protocol module `<origin>-<api>-compatible` maps to the shared `<origin>-compatible` extra. This applies to the per-provider check and to both module bundles.
- Tests: `tests/modules/test_openai_compatible.py` → `test_openai_speech_compatible.py`, `tests/modules/test_lazy_imports.py`, `tests/modules/test_tts_module_interface.py` (`_KINDS`, plus a test that the old name loads the same class and logs the warning, through both `load_module` and `list_voices`), `tests/audition/test_audition.py`, `tests-e2e/support.py`, `tests-e2e/test_voices.py`.
- `examples/config.openai-compatible.json` → `config.openai-speech-compatible.json`; `examples/auditions/reachy-mini.json` (the two candidates' `type`; their ids stay as they are so that saved choices still match).
- `benchmarks/matrix.json` (entry ids and type), `benchmarks/README.md`. The committed `results/20261027-1453_mac-m5-pro/` stays as measured, under the old name.
- `specs/modules/openai-compatible.md` → `openai-speech-compatible.md`: frontmatter, title, `type`, and a "Why the name" paragraph extended with the naming rule. `specs/tts-module-interface.md` gets the naming rule, the `RENAMED` table and `resolve_type`. `specs/project.md` gets the shared-extra rule and the install hint naming the extra. Also `specs/_index.md`, `specs/architecture.md`, `specs/testing.md`, `specs/ci.md`, `specs/modules/openai.md`.
- `docs/modules/openai-compatible.md` → `openai-speech-compatible.md`, plus every cross-reference in `docs/`, `AGENTS.md` and `README.md`.
- Older plans stay as history. The `pyproject.toml` comment above the extra and the committed result's `findings.md` link to the spec get only their path updated.

## Steps

1. `git mv` the package, spec, docs page, example and unit test file.
2. Rename the class, key and labels in code. Add `RENAMED` and `resolve_type`, and route `load_module`, `list_voices`, `bench.module_kind` and `audition.parse_module` through it.
3. Update the tests (extras mapping, alias test) and the live rows.
4. Rewrite the renamed spec and docs page, then update every cross-reference. Grep for `openai-compatible`, `openai_compatible` and `OpenAICompatible` across the repo to catch misses. The remaining hits should be only the extra, the error prefix, older plans, the committed benchmark result and `analysis/`.

## Verification

Run `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright` and `uv run pytest`. Then run the live row with `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k openai-speech-compatible'`.
