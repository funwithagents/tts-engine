---
code:
  - tests/conftest.py
  - tests-e2e/conftest.py
  - tests-e2e/support.py
tests:
---

# Testing

**Status:** Implemented

## Purpose

`tts-mcp`'s testing strategy — the two-tier structure and what a good test looks like. It's a **cross-cutting practice**, not a runtime concept: nothing here ships in the library. It exists as a spec so the decisions have one honest home that stays in sync with the setup, rather than living half in [project.md](project.md) (the tooling choices) and half in [AGENTS.md](../AGENTS.md) (the operational how-to). The concrete shell commands to run each tier live in [AGENTS.md](../AGENTS.md) "Testing".

## Two tiers, physically separated

Tests split into two directories, and the split is structural — a directory boundary, not a marker or an opt-out flag:

| Tier | Directory | Network | Deterministic | Runs by default |
|---|---|---|---|---|
| Unit / integration | `tests/` | never | yes | **yes** |
| Live / e2e | `tests-e2e/` | real ElevenLabs API + audio hw | no | **no** |

- **`tests/` is the normal dev loop.** Fast, deterministic, no real network, no credentials, no audio hardware. `pyproject.toml`'s `testpaths = ["tests"]` points the default `uv run pytest` here, so this is what runs on every change and what any contributor or CI can run with zero credentials.
- **`tests-e2e/` is opt-in.** It starts the real server as a subprocess, calls the `speak` tool over StreamableHTTP, and drives the real ElevenLabs API and the machine's audio output — network, credentials, non-deterministic — so it is deliberately *not* collected by the default run. Because `testpaths` already excludes it, no pytest marker or `--run-e2e` flag is needed: the physical separation is the whole mechanism. Run it explicitly (`uv run pytest tests-e2e`).

The `tests/` tier mirrors the `src/tts_mcp/` module layout (`test_<module>.py`, `modules/test_*.py`, plus the `test_project_map.py` drift-guard); `tests-e2e/` is organized around live scenarios rather than modules.

## What a good test asserts

- **Functional, not tautological.** Exercise what a feature actually does — inputs → outputs, state changes, side effects — not that it runs or matches its own signature. A test that would pass against a broken implementation (asserting a constant, that an object isn't `None`, that a mock was called) isn't worth writing.
- **Observable behavior only.** Assert return values, raised exceptions, calls to collaborators, and changes to public state. Never assert on private attributes (`_foo`). Drive the public API the way a real caller would.
- **One test per distinct code path.** Keep variants only when they trigger genuinely different logic; merge lifecycle sequences (start/stop, connect/disconnect) into one test. Error paths (`missing_key`, `empty_key`, `unknown_type`) are distinct scenarios and each deserve a test.
- **In the e2e tier, assert on behavior, not exact output.** Real service responses and audio vary run to run, so a live test asserts a robust property ("the `speak` call returned success", "audio bytes were produced"), never a specific string or audio content.

### Smell checklist

Delete or merge a test if it: asserts a private attribute; is fully subsumed by another test in the same file; checks something that cannot break independently; or is one of N near-identical tests differing only in which field they check.

### Speed budget

The full unit tier (`uv run pytest tests/`) must complete in under 5 seconds. If a test needs the network, a real device, or a sleep to pass, it belongs in `tests-e2e/`, not here.

## Live tier: skip without credentials

A live test needs real credentials, and it must **skip — never fail** — when they're absent, so you exercise only the services you hold keys for and a contributor (or CI) with none is never broken.

- The `server_url` fixture in `tests-e2e/conftest.py` skips the whole live test when `config.json` (which holds the ElevenLabs API key and voice) is absent from the repo root.
- `tests-e2e/support.require_env(NAME)` is the same guard for env-var secrets: it returns the variable or calls `pytest.skip(...)` when it's unset. Credentials come from `config.json`/the environment, never committed.

## Tooling

- **`pytest`** is the runner (`asyncio_mode = "auto"` for the async server/engine tests); **`ruff`** lints/formats; **`pyright`** (`standard` mode) type-checks. All three are the gate after any change — lint, type check, and tests must pass before work is considered done (see [AGENTS.md](../AGENTS.md), "Verification").
- **`pyright` covers test code too:** its `include` is `src`, `tests`, and `tests-e2e`, so tests are type-checked alongside the library rather than being a blind spot.

## Open questions

1. **CI wiring.** Nothing here sets up continuous integration. The default `tests/` tier is CI-ready (deterministic, no credentials), and the e2e tier skips cleanly when `config.json` is absent — but actually running either on a hosted runner is unbuilt. Today all testing is a local, manual command.
