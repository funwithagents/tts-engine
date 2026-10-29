# Benchmark: CPU and memory, finished-is-not-measured, the move to `benchmarks/`

**Status:** Done

Implements [specs/benchmark.md](../specs/benchmark.md) ("CPU and memory, from the worker's own process", "Finished is not measured", the `measures` field of a run, the resource columns of the summaries, open question 4). The benchmark records what each backend costs the machine beside how long it makes a caller wait, stops an all-error attempt from superseding a good measurement, and moves from `benchmarks/latency/` to the root of `benchmarks/`. It deliberately leaves out GPU usage (deferred, spec open question 4) and the re-measurement of the Mac result's local entries with the new fields (`RESULTS.md`, "Still to do"), which is a run, not code.

## Scope

- `benchmarks/bench.py` — `REPO_ROOT` one level up; `CpuSampler` (a thread recording the process's CPU time over windows, in percent of one core), `peak_rss_mb` (`resource`, bytes on macOS, kB on Linux, `None` on Windows), `host_state` (a Pi's SoC temperature and throttle flags); `measure_one` samples every call and records `cpu_log` and `cpu_s`; the `load` record carries `rss_mb`, a `usage` record after the runs carries the peak; `cpu_peak_pct` and `cpu_mean_pct` in `METRICS` / `derive_metrics`; `expected_cells` + `unmeasured_cells`, and the parent writes `done` only when no cell is unmeasured (else an abort naming them); `measures` and `cpu_count` in `env.json`; `load_mb` / `peak_mb` in `results.csv`, `CPU peak p50` / `p90` and `peak MB` in the compact summary, a `measures` column in the Runs table; `--cpu-window-ms`.
- `benchmarks/matrix.json`, `benchmarks/README.md`, `benchmarks/RESULTS.md` — moved; model paths, commands, the metric table, the design-choice bullet (with the measured sampler cost), the runs/results section, the options, a caveat; "Still to do" gains the Mac re-measurement.
- `benchmarks/results/20261027-1453_mac-m5-pro/` — moved; summaries regenerated (the new columns read `—`, the Runs table's `measures` column `—`), `description.md` says why.
- `tests/test_benchmark.py` — the script's new path; CPU metrics derived from a synthetic log and `None` without one; the sampler; `peak_rss_mb` / `host_state` on this platform; `unmeasured_cells`; a result merged from a run without resource records and one with them (cells, dashes, the Runs table); the new option.
- `specs/benchmark.md`, `specs/_index.md` — title, purpose, the new bullets, the summary columns, the not-measured states, open questions 3 and 4, the paths.
- `README.md`, `AGENTS.md`, `.gitignore`, `docs/`, `specs/modules/{cartesia,deepgram}.md`, `tools/voice-audition/README.md` — the path.

## Steps

1. Move `benchmarks/latency/*` to `benchmarks/` (the ignored `runs/` and `models/` locally), rewrite every path reference, fix `REPO_ROOT` and the test's script path; one commit.
2. `expected_cells` / `unmeasured_cells`; the parent records an abort instead of `done` when a cell has no successful measured run; a test on the rule.
3. `CpuSampler`, `peak_rss_mb`, `host_state`; the sampler around every call, `cpu_log` / `cpu_s` on the run record, `rss_mb` on `load`, the `usage` record; the two metrics; `cpu_count` and `measures` in `env.json`; the `results.csv` and summary columns, the Runs table column; `--cpu-window-ms`; tests.
4. Measure the sampler's cost to the headline on the Mac: three local entries, `say` on the short text, n = 20, `--cpu-window-ms 0` and 50, twice each; record the finding in the README and the spec.
5. The spec, its index row, the README, `RESULTS.md`, the Mac result's description and regenerated summaries, this plan.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks tools`, `uv run pyright`, `uv run pytest` clean, the unit tier under its 5 s budget.
- `bench.py run --only tone audiofile --runs 2 --runs-dir <scratch>`: the run records carry `cpu_s`, `cpu_log`, the `load` and `usage` records `rss_mb`, `env.json` `measures` and `cpu_count`; its summary shows the new columns and the Runs table's `measures`.
- `bench.py summarize benchmarks/results/20261027-1453_mac-m5-pro` reproduces every measured entry with `—` in the resource columns and `—` in the Runs table's `measures` column.
- The sampler cost measured (step 4) and written down.
