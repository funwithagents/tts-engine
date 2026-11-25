# Gradio demo: running from a config file, and a config app

**Status:** Done

Implements `specs/gradio-demo.md` as revised ("Running it", "The config app"): the demo's config becomes the engine block itself (app-setup's bare app), the demo speaks with a config file given with `--config` (its Setup tab read-only), and a config app edits one file with the Setup and Secrets panels. The example configs become engine blocks, the MCP server keeping its format through one example of its own. Out of scope: a config app creating a new file, and any change to the MCP server or `tts-engine-level` (both keep their formats).

## Scope

- `pyproject.toml`, `uv.lock` — app-setup at its `config-file` branch (`App(bare=True)`, `ConfigFile`, `app_setup.launch`, `config_page`), back to `main` once that branch is merged.
- `examples/gradio_demo/app.py` — `APP` bare; `open_model(argv)`: `add_config_arguments` (`--folder` | `--config`, `--env`) plus `--host`/`--port`, `open_source`, a missing file → `parser.error`; `main` over it.
- `examples/gradio_demo/default_config.json` — the engine block, unwrapped.
- `examples/gradio_demo/__main__.py` — the new options in its usage line.
- `examples/config_app/__init__.py`, `examples/config_app/__main__.py` — **new**: `open_model(argv)` (`add_config_app_arguments`, `open_config_app`), `main` launching `config_page`.
- `examples/config.<module>.json` → `examples/configs/<module>.json` (`git mv`, then the `engine` block alone; the audiofile example's fixture paths one level further up).
- `examples/mcp/config.json` — **new**: a `server` block around the tone engine.
- `.gitignore` — `.env`; `examples/mcp/config.json` excepted from the `config.json` rule.
- `tests/examples/test_gradio_demo.py` — the bare default; the three launches, the read-only tab, a missing file, an example run as it is, the config app saving the bare document.
- `tests/test_setup.py` — the examples read from `examples/configs/` as engine blocks; one example per registered module.
- `tests/test_config.py` — the MCP example loads; an example engine dropped into it loads.
- `specs/gradio-demo.md`, `specs/_index.md`, `specs/configuration.md`, `specs/project.md`, `specs/setup.md`, `specs/modules/{kokoro,phonon,piper,qwen3}.md` — the launches, the config app, the examples' new place and shape.
- `README.md`, `AGENTS.md`, `docs/setup.md`, `docs/configuration.md`, `docs/agents-and-mcp.md`, `docs/modules/*.md` — the same, "a complete engine config", the MCP example.

## Steps

1. Point app-setup at `config-file`; lock and sync.
2. Move and unwrap the example configs; add the MCP example; update every reference.
3. Make the demo bare and give it `--config`/`--env`; add the config app.
4. Tests, specs, docs.

## Verification

`uv run ruff check .`, `uv run ruff format src tests tests-e2e benchmarks examples`, `uv run pyright`, `uv run pytest`; the demo started from `examples/configs/tone.json` and the config app over a copy of it, each serving its page.
