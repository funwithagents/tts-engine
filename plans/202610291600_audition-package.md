# Packaged voice audition and the Reachy Mini example

**Status:** Done

Implements the updated `specs/voice-audition.md` ("Packaging", "Examples", the `description` field, "Starting choices"): the audition tool moves from `tools/voice-audition/` into the package as the `tts-engine-audition` command, gains a description shown on the page and starting choices embedded with `--choices`, and ships the audition that chose the Reachy Mini robot's voices as its worked example, hosted on GitHub Pages. Several sentences, `select` and rendering with a gain stay open questions.

## Scope

- `src/tts_engine/audition/__init__.py` — the subpackage, a docstring only
- `src/tts_engine/audition/audition.py` — moved from `tools/voice-audition/audition.py`: the template read with `importlib.resources`, `description`, `load_choices`, `new` taking keyword arguments instead of an `argparse.Namespace`, the choices passed to the page
- `src/tts_engine/audition/cli.py` — the command line split out of `audition.py`, plus `--log-level` and `--choices`
- `src/tts_engine/audition/page.html` — moved from `tools/voice-audition/page.html`: the description block, the starting choices when the browser has none stored
- `pyproject.toml` — the `tts-engine-audition` script; pyright no longer includes `tools`
- `tests/audition/test_audition.py` — moved from `tests/test_voice_audition.py`: imports the package, the pinned cache key, the description, `--choices` and its refusals, the packaging, the examples
- `examples/auditions/demo.json` — moved from `tools/voice-audition/examples/demo.json`
- `examples/auditions/reachy-mini.json`, `examples/auditions/reachy-mini-choices.json` — from the untracked `analysis/reachy-voices/`: renamed `reachy-mini`, a description, `gradium-fennick` (rejected by Gradium), nine kokoro voices (all "no") and `piper-danny-low` (a "no" whose data licence could not be verified) dropped, the kokoro model path pointed at `benchmarks/models/`
- `docs/voice-audition.md` — moved from `tools/voice-audition/README.md`, rewritten for the installed command, with the examples
- `README.md`, `docs/choosing-a-backend-and-voice.md`, `docs/configuration.md`, `docs/index.md` — the command and the new links
- `AGENTS.md`, `specs/project.md`, `specs/ci.md`, `.github/workflows/ci.yml`, `tests/test_docs.py`, `.gitignore` — `tools/` gone: map rows, the format-check directories, the third entry point and the subpackage rule
- `specs/voice-audition.md`, `specs/_index.md`, `plans/_index.md` — the spec, its index row, this plan's row
- `gh-pages` branch (not `main`) — `auditions/reachy-mini/`: the rendered page, `results.json`, the samples and `CREDITS.md`

## Steps

1. **Move** the files with `git mv`, then remove `tools/`.
2. **Split** the command line into `cli.py`; `audition.py` keeps the logic and reads the template as package data. The cache key is unchanged and pinned by a test, so existing renders are reused.
3. **`description`**: validated as an optional string, embedded in the page data, rendered as paragraphs with `[label](href)` links limited to http(s) and relative targets.
4. **`--choices`**: `load_choices` checks the file strictly (audition name, known ids, one list per id, lists of strings) before any synthesis; the page uses the embedded choices only when the browser has none stored.
5. **The Reachy Mini example**: build the two files from `analysis/reachy-voices/`; the analysis notes are not committed.
6. **Docs and wiring**: the docs page, the README section, the map, the specs naming the format directories and the entry point.
7. **Render** from a copy of the existing render (`analysis/reachy-voices/reachy-voices.audition/`), so the samples that were listened to are kept: the 57 unchanged candidates get their levels from their WAVs with no synthesis, and the four kokoro candidates, whose model path changed, are rendered again locally (after the running benchmark, so it is not disturbed). Remove the WAVs of the dropped candidates.
8. **Publish**: an orphan `gh-pages` branch with `auditions/reachy-mini/` (the rendered folder and `CREDITS.md`), pushed, then Pages enabled from that branch (`gh api -X POST repos/funwithagents/tts-engine/pages -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'`). Check <https://funwithagents.github.io/tts-engine/auditions/reachy-mini/> opens on the choices.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` (the docs and project-map checks included); `uv build` ships `page.html` and the three entry points; the hosted page loads, plays and shows the starting choices. Then this plan is `Done` and the spec `Implemented`.
