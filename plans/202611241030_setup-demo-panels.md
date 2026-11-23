# Setup demo: its own page around module-setup's panels

**Status:** Done

Implements `specs/setup-demo.md` as revised: module-setup no longer draws a page or runs sessions, so the demo builds its page from the Setup and Secrets panels and runs its own sessions.

## Scope

- `examples/setup_demo/app.py` — `APP` without `start` and `session_ui`; `build_page(model, sessions)`: the Session tab (Start, Stop, state, Speak, Interrupt), then `setup_panel` and `secrets_panel` following its draft; `main`: `--folder`, `--host`, `--port`, `--start`, the model locked by the sessions, the session stopped when the page stops.
- `examples/setup_demo/session.py` — `build_session(section, folder)` (was `start`), `Sessions`: one session at a time, started from the saved config and refused with the model's blockers, the config locked while it starts, runs or stops.
- `examples/setup_demo/__main__.py` — the new options in its usage line.
- `tests/examples/test_setup_demo.py` — the sessions and the page as above.
- `pyproject.toml`, `uv.lock` — module-setup at its pull request's new commit.
- `specs/setup-demo.md`, `specs/_index.md`, `specs/setup.md`, `docs/setup.md` — the panels, the options, no settings file.

## Steps

1. Move the session's lifecycle into `Sessions`, with the lock the model reads.
2. Build the page from the panels; parse the demo's own options.
3. Rewrite the tests; run the page in a browser.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e examples`, `uv run pyright`, `uv run pytest`; the page driven in a headless browser.
