# Setup demo: its own page around module-setup's panels

**Status:** Done

Implements `specs/setup-demo.md` as revised: module-setup no longer draws a page or runs sessions, so the demo builds its page from the Setup and Secrets panels and builds its own engine. The engine has nothing to start: it is built when Speak needs it, from the saved config, and rebuilt after a save, so the demo has no Start, no Stop and no lock.

## Scope

- `examples/setup_demo/app.py` — `APP` without `start` and `session_ui`; `build_page(model, voice)`: the Speak tab (Speak, Interrupt), then `setup_panel` and `secrets_panel` following its draft; `main`: `--folder`, `--host`, `--port`, the model with no lock, the voice closed when the page stops.
- `examples/setup_demo/session.py` — `build_session(section, folder)` (was `start`), `Voice`: the session built from the saved config at the utterance that needs it, refused with the model's blockers, kept while the `tts` section is unchanged, rebuilt after a save; one utterance at a time.
- `examples/setup_demo/__main__.py` — the new options in its usage line.
- `tests/examples/test_setup_demo.py` — the voice and the page as above.
- `pyproject.toml`, `uv.lock` — module-setup at its pull request's new commit.
- `specs/setup-demo.md`, `specs/_index.md`, `specs/setup.md`, `docs/setup.md` — the panels, the options, no settings file, no session to start.

## Steps

1. Build the engine on demand in `Voice`, rebuilt when the saved section changes.
2. Build the page from the panels; parse the demo's own options.
3. Rewrite the tests; run the page in a browser.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e examples`, `uv run pyright`, `uv run pytest`; the page driven in a headless browser.
