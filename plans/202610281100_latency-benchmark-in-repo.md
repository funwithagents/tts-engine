# Latency benchmark into the repository

**Status:** Done

Implements [specs/benchmark.md](../specs/benchmark.md) (all sections). The latency benchmark written and run under the uncommitted `analysis/` folder on 2026-10-27/28 moves into the repository as `benchmarks/latency/`, gains the runs/results layout (ignored runs, committed results merged from them, with a description, a manifest, packed records and summaries), a unit test for its pure parts, and its place in the project map and quality gates. It deliberately leaves out cross-bench comparison and charts (spec open questions 1 and 2) and the Raspberry Pi notes, which stay a local working document until a Pi bench exists.

## Scope

- `benchmarks/latency/bench.py` — the script (was `analysis/ttfa-benchmark/bench_ttfa.py`), now with subcommands: `run` (into `runs/<timestamp>/`, `--into <result>` to merge when it ends, `--resume`), `new` (a dated result folder with a description template), `merge` (runs into a result: `merge_runs` precedence, `runs.json` manifest, packed `raw.jsonl.gz`, `results.csv` per cell and the compact one-row-per-entry `summary.csv`/`summary.md`), `summarize` (a result or a single run), `list`, hidden `worker`.
- `benchmarks/latency/matrix.json` — unchanged entries; the kokoro opt-in model paths point at `benchmarks/latency/models/`.
- `benchmarks/latency/README.md` — what is measured and why, the matrix table, how to run, stop and resume, the results layout, caveats, the model catalog; paths and commands updated.
- `benchmarks/latency/RESULTS.md` — the findings so far (the Mac bench), what each run contributed, the decisions it fed, what remains.
- `benchmarks/latency/results/20261027-1453_mac-m5-pro/` — the Mac result: `description.md`, `runs.json`, the merged `raw.jsonl.gz` and summaries, built from the runs so far (kept locally under `runs/`).
- `tests/test_benchmark.py` — metric derivation, statistics, selection and skip rules, the token stream, the CLI, run merging precedence, a result built then overridden by a newer run (manifest, packed records, summaries, description untouched), `new`, a single-run summary.
- `specs/benchmark.md`, `specs/_index.md` — the spec and its row. `AGENTS.md` — `benchmarks/` in the top-level layout, `benchmarks` in the format command. `README.md` — a "Latency benchmark" section. `pyproject.toml` — `benchmarks` in pyright's include. `.gitignore` — `runs/`, models, stdout logs.

## Steps

1. Rewrite the orchestration, merging, summary and CLI halves of the script: runs into `runs/`, results with `description.md`, `runs.json`, packed records and summaries, the merge rule, the subcommands.
2. Move the folder to `benchmarks/latency/`, renaming the script to `bench.py`; move the existing run directories into `runs/` (plain raw), create `results/20261027-1453_mac-m5-pro` with `new --from-run` and merge every run into it; fix the matrix's model paths; delete the worker stdout logs.
3. `.gitignore` and `pyproject.toml` (pyright include).
4. `tests/test_benchmark.py` loading the script by path.
5. `specs/benchmark.md` (Implemented) and its index row; this plan and its index row; `AGENTS.md` map and commands; the README pointer.
6. Rewrite the bench README for the new paths and layout; write `RESULTS.md` from the local status notes, leaving the Raspberry Pi sections in `analysis/`.
7. Run a smoke pass on the `tone` fixture (`run --only tone --runs 2`) to exercise the whole pipeline, and check `runs/` is ignored by git.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` clean, the unit tier under its 5 s budget.
- `uv run python benchmarks/latency/bench.py run --only tone --runs 2` writes `runs/<ts>/raw.jsonl` and a summary; `git status` shows nothing under `runs/`.
- `uv run python benchmarks/latency/bench.py summarize benchmarks/latency/results/20261027-1453_mac-m5-pro` reproduces the measured entries with their run ids from the packed state alone.
