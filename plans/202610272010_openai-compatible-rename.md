# Rename `openai` → `openai-compatible`

**Status:** Done

Implements [specs/modules/openai-compatible.md](../specs/modules/openai-compatible.md) ("Why the name", open question 1), following item 3 of `analysis/api-currency-audit.md` and the rule in [specs/project.md](../specs/project.md) ("Dependency strategy for TTS backends") that a module follows the provider's current API. OpenAI's `/v1/audio/speech` loses every OpenAI model on 2027-01-06 and its successor is Realtime-only, but the endpoint lives on through compatible servers, so today's module is renamed for the endpoint and the vendor key `openai` is freed for the Realtime module (audit item 5). A pure rename: no behaviour changes. It leaves out the Realtime module and the post-shutdown removal of the hosted default (audit item 7, now open question 1 of the renamed spec).

## Scope

- `src/tts_engine/modules/openai/` → `src/tts_engine/modules/openai_compatible/` — `OpenAIModule` → `OpenAICompatibleModule`; `_LABEL`, the install hint (`tts-engine[openai-compatible]`), the `TTSError` prefix (`OpenAI-compatible request failed`), the `cleanup` label and the docstrings.
- `src/tts_engine/modules/__init__.py` — `REGISTRY["openai-compatible"]`; `"openai"` is unregistered until the Realtime module exists.
- `pyproject.toml`, `uv.lock` — extra `openai-compatible = ["openai>=3.37,<4"]`, the `all` aggregate.
- `examples/config.openai.json` → `examples/config.openai-compatible.json`.
- `tests/modules/test_openai.py` → `tests/modules/test_openai_compatible.py`; `tests/modules/test_lazy_imports.py` (registry key and construction row; the poisoned import stays `openai`); `tests-e2e/support.py` (`_REQUIRED_IMPORT`, the `MODULES` row id `openai-compatible`, same key and `tts-1`).
- `specs/modules/openai.md` → `specs/modules/openai-compatible.md` — frontmatter, title, `type`, extra, error strings, the "Why the name" paragraph, the `base_url` deferral as open question 1. Status stays `Implemented`: spec and code change together.
- `specs/_index.md`, `specs/project.md`, `specs/architecture.md`, `specs/tts-module-interface.md`, `specs/testing.md`, `specs/ci.md`, `AGENTS.md`, `README.md` — every mention of the key, package, extra, example and spec path.
- `plans/202610261647_openai-module.md` — one line pointing at this plan; otherwise left as history.

## Steps

1. `git mv` the package, spec, example and unit test file.
2. Rename the class, key, extra and strings in code; `uv lock`.
3. Update the tests and the live row; run the unit tier (the project-map test flags the stale frontmatter until step 4).
4. Rewrite the renamed spec and update every cross-reference; grep for `openai` across the repo (excluding `analysis/` and the SDK import name) to find misses.
5. `.github/workflows/ci.yml` names only the `OPENAI_API_KEY` secret, which is unchanged: nothing to edit.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest`; live `-k openai-compatible` via `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k openai-compatible'`.
