---
code:
  - pyproject.toml
  - tests/conftest.py
  - tests-e2e/conftest.py
  - tests-e2e/support.py
  - tests-e2e/test_modules.py
  - tests-e2e/test_mcp.py
tests:
---

# Testing

**Status:** Implemented

## Purpose

`tts-engine`'s testing strategy — the two-tier structure and what a good test looks like. It's a **cross-cutting practice**, not a runtime concept: nothing here ships in the library. It exists as a spec so the decisions have one honest home that stays in sync with the setup, rather than living half in [project.md](project.md) (the tooling choices) and half in [AGENTS.md](../AGENTS.md) (the operational how-to). The concrete shell commands to run each tier live in [AGENTS.md](../AGENTS.md) "Testing".

## Two tiers, physically separated

Tests split into two directories, and the split is structural — a directory boundary, not a marker or an opt-out flag:

| Tier | Directory | Network | Deterministic | Runs by default |
|---|---|---|---|---|
| Unit / integration | `tests/` | never | yes | **yes** |
| Live / e2e | `tests-e2e/` | real ElevenLabs API + audio hw | no | **no** |

- **`tests/` is the normal dev loop.** Fast, deterministic, no real network, no credentials, no audio hardware. `pyproject.toml`'s `testpaths = ["tests"]` points the default `uv run pytest` here, so this is what runs on every change and what any contributor or CI can run with zero credentials.
- **`tests-e2e/` is opt-in.** It drives the real ElevenLabs API and the machine's audio output — network, credentials, non-deterministic — so it is deliberately *not* collected by the default run. Because `testpaths` already excludes it, no pytest marker or `--run-e2e` flag is needed: the physical separation is the whole mechanism. Run it explicitly (`uv run pytest tests-e2e`).

**Configs are hardcoded in `support.py`, not a committed file.** The live tier carries no `config.json`; every module config lives in code (asr-engine style), and the MCP subprocess is handed a temp config the helpers build from those dicts. This keeps the configs, the skip gates, and the backend table in one reviewed place.

The live tier splits by **what actually varies when the TTS module changes** — the backend contract (and the engine's full library path) is per-module; only the MCP transport is module-agnostic and runs once against a **default module**:

- **Per module — `test_modules.py`** (parametrized, engine-direct). Built from the **`MODULES` table** in `support.py` — one `pytest.param(module_type, module_config, ...)` row per backend, each carrying its own dedicated config (voice/model, `api_key_env`, backend-specific fields). **Adding a module means adding one row.** Each backend runs two scenarios, both asserting robust properties only, never audio content:
  - `test_module_say_produces_pcm` — synthesize into an injected `AudioSink` (`TTSEngine(cfg, sink=CaptureSink())`, no audio hardware); assert **whole signed-16-bit PCM** was produced, drained once, `sample_rate > 0`.
  - `test_module_say_completes` — the same synthesis through the default `AudioPlayer` sink, so the output stream is opened at *that module's* declared sample rate on real audio hardware; assert the pass completes without raising.
- **Module-agnostic — `test_mcp.py`** (default module). The MCP transport doesn't change with the backend, so it runs once: it starts a `tts-engine-mcp` subprocess (temp config built from `default_module()`) and calls the `say` tool over StreamableHTTP with a real MCP client. Uses the `server_url` fixture.

**Default module vs per-module configs.** `support.default_module()` is the single place the *default backend* the module-agnostic MCP test drives is chosen; the `MODULES` table holds the *per-module configs* for the parametrized conformance tests. They share one config dict for the reference backend (elevenlabs) so it isn't duplicated, but serve different tests.

The `tests/` tier mirrors the `src/tts_engine/` module layout (`test_<module>.py` — e.g. `test_engine.py`, `test_tools.py`, `test_mcp.py`, `test_config.py` — one `modules/test_<backend>.py` per TTS module, plus the `test_project_map.py` drift-guard); `tests-e2e/` is organized around these live scenarios rather than modules.

## What a good test asserts

- **Functional, not tautological.** Exercise what a feature actually does — inputs → outputs, state changes, side effects — not that it runs or matches its own signature. A bare call-count assertion is insufficient; collaborator assertions should verify meaningful arguments, ordering, or externally observable effects and would fail against a broken implementation.
- **Observable behavior only.** Assert return values, raised exceptions, calls to collaborators, and changes to public state. Never assert on private attributes (`_foo`). Drive the public API the way a real caller would.
- **One test per distinct code path.** Keep variants only when they trigger genuinely different logic; merge lifecycle sequences (start/stop, connect/disconnect) into one test. Error paths (`missing_key`, `empty_key`, `unknown_type`) are distinct scenarios and each deserve a test.
- **In the e2e tier, assert on behavior, not exact output.** Real service responses and audio vary run to run, so a live test asserts a robust property ("the `say` call returned success", "audio bytes were produced"), never a specific string or audio content.

### Smell checklist

Delete or merge a test if it: asserts a private implementation attribute; is fully subsumed by another test in the same file; checks something that cannot break independently; or is one of N near-identical tests differing only in which field they check. Tests should drive public methods such as `FastMCP.call_tool`; provider configuration is observed through the arguments sent to the provider client.

### Speed budget

The full unit tier (`uv run pytest tests/`) must complete in under 5 seconds. If a test needs the network, a real device, or a sleep to pass, it belongs in `tests-e2e/`, not here.

## Live tier: skip without credentials

A live test needs real credentials, and it must **skip — never fail** — when they're absent, so you exercise only the services you hold keys for and a contributor (or CI) with none is never broken.

- **No secrets in the tree.** The hardcoded module configs carry `api_key_env: "ELEVENLABS_API_KEY"` — the *name* of the env var holding the key, never the key itself (see [elevenlabs-module.md](elevenlabs-module.md), "API key resolution"). The live tier is turned on by exporting `ELEVENLABS_API_KEY`; the MCP subprocess inherits the parent environment, so the same variable reaches the server it spawns. This is what makes the tier CI-ready.
- **`support.require_module(module_type, config)` is the skip gate**, and both entry points funnel through it — `default_module()` calls it before returning, and the parametrized `test_modules.py` calls it per row. Backends gate differently, so it generalizes the key check: it skips when either (a) the config's `api_key_env` names an unset variable (an API backend without its key, like elevenlabs), **or** (b) the backend's optional library isn't importable (a local-model backend whose packaging extra isn't installed, like `pocket` needing `pocket_tts` — see [project.md](project.md), "Dependency strategy for TTS backends"). A backend that needs no key and whose library is present never skips. The module type → gating-library map lives beside the `MODULES` table.

## Tooling

- **`pytest`** is the runner (`asyncio_mode = "auto"` for the async server/engine tests); **`ruff`** lints and formats; **`pyright`** (`standard` mode) type-checks. Lint, type check, and tests must all pass before work is considered done (see [AGENTS.md](../AGENTS.md), "Verification"/"Commands").
- **`pyright` covers test code too:** its `include` is `src`, `tests`, and `tests-e2e`, so tests are type-checked alongside the library rather than being a blind spot.

## Open questions

1. **CI wiring.** Nothing here sets up continuous integration. The default `tests/` tier is CI-ready (deterministic, no credentials), and the e2e tier skips cleanly when credentials are absent — but actually running either on a hosted runner is unbuilt. Today all testing is a local, manual command.
