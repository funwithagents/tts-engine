# Library-idiomatic logging; drop config log level

**Status:** Done

Implements the standard library-vs-application logging split settled in [project.md](../specs/project.md) ("Logging") and [configuration.md](../specs/configuration.md): the library becomes a well-behaved logging citizen (a `NullHandler` on the package logger, nothing else), and the log level moves out of the config file to the MCP server's `--log-level` flag. The MCP entry point — the only caller that configures logging — does so the textbook application way, with `logging.basicConfig` on the root logger, which lets the package-scoped `setup_logging` helper (and `_logging.py`) be deleted entirely. Deliberately leaves the module-level `log = logging.getLogger(__name__)` convention as-is.

## Scope

- `src/tts_engine/__init__.py` — attach `logging.NullHandler()` to `logging.getLogger("tts_engine")` so `import tts_engine` is silent and configures nothing.
- `src/tts_engine/config.py` — remove `LoggingConfig`, the `logging` field on `AppConfig`, `_VALID_LEVELS`, and the `logging` parsing/validation block.
- `src/tts_engine/_logging.py` — **deleted**. It only ever served the MCP entry point, which now configures logging itself; the package-scoped handler / `propagate` juggling is gone.
- `src/tts_engine/mcp_server_cli.py` — add a `--log-level` argument (choices `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`, default `INFO`); configure logging with `logging.basicConfig(level=args.log_level, format=_LOG_FORMAT)` instead of `setup_logging(cfg.logging.level)`.
- `config.example.json` — drop the `logging` block.
- Specs: [configuration.md](../specs/configuration.md) (remove the `logging` block, `LoggingConfig`, validation rule), [project.md](../specs/project.md) (rewrite "Logging" for the library/app split + `--log-level`), [mcp-server.md](../specs/mcp-server.md), [architecture.md](../specs/architecture.md), [overview.md](../specs/overview.md), [_index.md](../specs/_index.md).
- Docs: `AGENTS.md` (config block, prose, "Logging conventions"), `README.md` (config block, field table, run example).
- Docs: `AGENTS.md` (project map — drop the `_logging.py` row, remove it from the module list; config block; prose; "Logging conventions"), `README.md`.
- `tests/test_config.py` — drop the two `logging.level` tests and all `logging` references in fixtures/assertions; rename the server-defaults test.
- `tests/test_mcp_server_cli.py` — pass `--log-level WARNING` and assert it reaches `logging.basicConfig`; add a test that the level defaults to `INFO`.

## Steps

1. Add the `NullHandler` in `__init__.py`.
2. Strip logging from `config.py`.
3. Add `--log-level` to `mcp_server_cli.py`; configure via `logging.basicConfig`; delete `_logging.py`.
4. Update `config.example.json`, the committed `tests-e2e/config.json`, and all specs/docs (including removing `_logging.py` from the project map + spec frontmatter).
5. Update `tests/test_config.py` and `tests/test_mcp_server_cli.py`.
6. Run the verification gate.

## Verification

- `tests/test_config.py` no longer references `logging`; `tests/test_mcp_server_cli.py` covers both an explicit `--log-level` and the `INFO` default.
- Gate: `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass.
- The touched specs stay `Implemented` (spec + code updated in the same change). Mark this plan `Done` in [_index.md](_index.md).
