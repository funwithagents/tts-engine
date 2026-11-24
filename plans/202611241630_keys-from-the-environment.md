# Keys never in a config

**Status:** Done

Implements the key rule in `specs/modules/elevenlabs.md` ("API key resolution"), `specs/tts-module-interface.md` ("Shared config helpers"), `specs/setup.md` and `specs/configuration.md`: a hosted module finds its API key under a variable name, the one `api_key_env` names or the provider's usual one by default, in the secrets the application gives in code (`TTSEngine(config, secrets=...)`) and then in the environment; a config never holds the key itself. Leaves out the other libraries of an app, which follow their own specs.

## Scope

- `src/tts_engine/modules/base.py` — `key_env_field(provider, variable)` declares `api_key_env` with its default; `refuse_api_key` and `TTSModule._refuse_key` refuse an `api_key` field in `check_config` and `validated`; the key lookup (now `TTSModule.resolve_api_key`, below) reads the named variable or the default; `_key_items` reports the default variable when the field is absent.
- `src/tts_engine/modules/{elevenlabs,gradium,mistral,deepgram,gemini,cartesia,openai}/module.py` — one `key_env_field` instead of the `api_key` and `api_key_env` fields; the provider's usual variable given to `key_env_field`.
- `src/tts_engine/modules/openai_speech_compatible/module.py` — `api_key_env` with no default; absent, the client gets the placeholder key `"unused"`, so `OPENAI_API_KEY` is never sent to a server the config did not give it to.
- `src/tts_engine/config.py` — `MissingSecretError(ConfigError)`, carrying `variable` and `label`: no key under the variable, in the secrets given in code nor the environment.
- `src/tts_engine/modules/base.py`, `src/tts_engine/modules/__init__.py`, `src/tts_engine/engine.py` — `TTSModule.resolve_api_key(config, secrets)`, a classmethod taking the label from the module's title and the default from its `api_key_env` field (the free `resolve_api_key(config, *, label, default)` and the modules' `_KEY_ENV` constants are gone); `TTSModule.takes_secrets()`; `load_module(config, secrets=...)` and `list_voices(config, secrets=...)` hand `secrets` only to a module that reads a key; `TTSEngine(config, secrets=...)`. The hosted modules' constructors and `list_voices` take `secrets` as a keyword.
- `src/tts_engine/audition/audition.py` — `_missing_key` no longer looks at `api_key` and checks the module's default variable when none is named; `parse_module` writes the module's own default variable (none for `openai-speech-compatible`); the page still redacts an `api_key` left in an older audition file.
- `tests/conftest.py` — every provider's default variable set to `"k"` for every test, the shell's real keys kept out.
- `tests/modules/*.py`, `tests/test_setup.py`, `tests/test_config.py` — configs without `api_key`; the shared rule's tests rewritten; every provider module's default variable, an unset one reported missing, a key in the config refused; a compatible server naming no key.
- `specs/modules/*.md`, `specs/tts-module-interface.md`, `specs/setup.md`, `specs/configuration.md`, `specs/voice-audition.md`, `specs/_index.md`, `docs/configuration.md`, `docs/modules/*.md`, `docs/voice-audition.md` — the rule, the field tables, the refusal message.

## Steps

1. Declare the key field once, with its default, and refuse `api_key`.
2. Convert the eight hosted modules; give `openai-speech-compatible` its keyless placeholder.
3. Set the default variables for the tests and drop the written keys from their configs; rewrite the key tests.
4. Update the specs and the docs.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks examples`, `uv run pyright`, `uv run pytest`.
