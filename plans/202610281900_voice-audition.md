# Voice audition tool

**Status:** Done

Implements `specs/voice-audition.md`: a `tools/voice-audition/audition.py` CLI with `new` (an audition file from the backends' catalogs through `list_voices`), `render` (every candidate synthesized through `TTSEngine`, its time to first audio measured over a few runs after a warm-up, re-rendering only what changed) and `page` (the listening page with Yes / Maybe / No / NotSet choices, Yes and Maybe tabs, export and import). One sentence per audition; no `select` command and no packaged entry point (the spec's open questions).

## Scope

- `tools/voice-audition/audition.py` — the CLI: file loading and validation, `new`, `render`, `page`
- `tools/voice-audition/page.html` — the page template: styles, the script that renders rows from the embedded data, choices, tabs, chained playback, export/import
- `tools/voice-audition/examples/demo.json` — a small hand-written audition, one voice per backend
- `tests/test_voice_audition.py` — fast-tier tests (faked `list_voices`, the `tone` fixture)
- `.gitignore` — `*.audition/` output folders
- `pyproject.toml` — pyright includes `tools`
- `.github/workflows/ci.yml` — the format check covers `tools`
- `AGENTS.md` — project map row for `tools/`, the format command
- `README.md` — a "Voice audition" section
- `specs/voice-audition.md`, `specs/_index.md`, `specs/ci.md` — the spec, its index row, the format-check paths
- `plans/_index.md` — this plan's row

## Steps

1. **Loading.** `load_audition(path)` returns the parsed file with defaults filled in (`name` from the stem, `label` from the id, `section` from `config.type`), raising `AuditionError` with the file and field for every rule of the spec's table, unknown keys included.
2. **`new`.** Parse each `--module` (a type, or a JSON object when it starts with `{`); a bare hosted type gets `api_key_env` from the convention (`REGISTRY[type].kind`). List with `tts_engine.list_voices`, print and continue on `VoiceListingUnsupported` / `ConfigError` / `TTSError`. Filter (gender, language prefix, query, `--keep-unknown`), cut to `--limit`, build ids (slug, deduplicated), notes and merged configs, write with `indent=2`; refuse an existing file without `--force`; exit 1 when no voice was found.
3. **`render`.** For each selected candidate: the cache key (`sha256` of the canonical JSON of text and config); skip unchanged `ok` entries unless `--force`; `skipped` for an unset `api_key_env` or a missing extra (the `ConfigError` names `extra`); else build `TTSEngine(TTSEngineConfig.from_dict({"module": config}, base_dir=audition dir), sink=capture)`, one warm-up `say`, `--runs` timed `say`s, write the last run's WAV, close the engine; record the entry and write `results.json` after each candidate. Drop entries of removed ids. Rewrite the page at the end; exit 1 if any `error`.
4. **`page`.** Embed `{name, text, sections, candidates (label, notes, redacted config), results}` into `page.html` at its data placeholder (`</` escaped so the JSON cannot close the script tag) and write `index.html`.
5. **Template.** Rows rendered client-side from the data; the four choice buttons; tabs with counts; dimming and highlighting; chained playback over the visible players; `localStorage` under `voice-audition:<name>` wrapped in try/catch; export and import in the spec's format.
6. **Example and wiring.** The example audition; `.gitignore`, pyright include, CI format paths, AGENTS map and commands, README section, spec index row.

## Verification

- `uv run pytest tests/test_voice_audition.py` and the full `uv run pytest`.
- A real run: `render` of the example audition with the keys sourced, the page opened, choices made, exported and re-imported.
- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks tools`, `uv run pyright`.
