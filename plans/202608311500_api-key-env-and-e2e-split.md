# API key from env + split the e2e tier by interface

**Status:** Done

Implements the settled behavior in `specs/elevenlabs-module.md` ("API key resolution") and `specs/testing.md` ("Two tiers"). Adds an `api_key_env` config field so `config.json` can be committed without a secret, and splits the live tier into one file per interface (library vs MCP). Leaves the module contract, the audio path, and audio-content verification untouched.

## Scope

- `src/tts_engine/modules/elevenlabs.py` — resolve the API key from a literal `api_key` or the env var named by `api_key_env`.
- `tests/modules/test_elevenlabs.py` — functional tests for env resolution, literal precedence, and the unset-var error.
- `tests-e2e/test_speak.py` → `tests-e2e/test_mcp.py` — renamed; still the StreamableHTTP `speak`-tool path.
- `tests-e2e/test_engine.py` — new; in-process library path (`TTSEngine.from_config → speak`).
- `tests-e2e/conftest.py` — add an `app_config` fixture, skipping when credentials are unavailable.
- `tests-e2e/config.json` + `.gitignore` — commit a secret-free e2e config (`api_key_env: ELEVENLABS_API_KEY`), un-ignored past the repo-wide `config.json` rule; the live tier keys off the env var, not a dropped-in file.
- `tests-e2e/support.py` — point `CONFIG_PATH` at the committed config; add `require_e2e_config()` (the skip-unless-credentials gate) used by both fixtures.
- `config.example.json`, `specs/elevenlabs-module.md`, `specs/configuration.md`, `specs/testing.md`, `AGENTS.md` — document `api_key_env`, the committed e2e config, and the two-file e2e split.

## Steps

1. Add `ElevenLabsModule._resolve_api_key(config)`: literal `api_key` wins; else read `os.environ[api_key_env]` (raise `ConfigError` naming the var if unset/empty); else raise `ConfigError` requiring one of the two.
2. Add unit tests: env resolution, literal-over-env precedence, unset-var error. Existing missing/empty-key tests still pass.
3. `git mv tests-e2e/test_speak.py tests-e2e/test_mcp.py`; refresh its docstring.
4. Add the `app_config` fixture to `conftest.py` and `tests-e2e/test_engine.py` driving `TTSEngine.from_config(cfg.engine).speak(...)`.
5. Update the specs, `config.example.json`, and `AGENTS.md`; refresh the `tests:` frontmatter in `elevenlabs-module.md` (`test_speak.py` → `test_engine.py`, `test_mcp.py`).

## Verification

`uv run ruff check .`, `uv run pyright`, `uv run pytest` all green (including `test_project_map.py`'s frontmatter drift-guards). Live tier spot-checked with a real `config.json` via `uv run pytest tests-e2e`. Marked `Done` here and in [_index.md](_index.md) once they pass.
