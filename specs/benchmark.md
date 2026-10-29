---
code:
  - benchmarks/bench.py
  - benchmarks/matrix.json
tests:
  - tests/test_benchmark.py
---

# Latency Benchmark

**Status:** Implemented

## Purpose

The engine exists for low-latency streaming playback, so the project needs one repeatable way to answer "how long does a caller wait between asking to speak and the first sound, per backend and model, and does the audio then keep up?". The benchmark in [`benchmarks/`](../benchmarks/) measures that at the engine's public API, the `TTSEngine` an embedder or the MCP `say` tool uses, not inside each SDK, so the numbers include everything a user of this library pays: sentence grouping, socket setup, the module's buffering policy. Its findings feed design decisions recorded in the module specs (a module default, a held socket, a live-row pin) and are kept in the repository as data, so a decision can be re-checked against the measurement that made it.

It is tooling, not part of the library: nothing in `src/` depends on it, it is not installed, and it is never run by CI (it needs keys, hardware and hours).

## Decided

### Measurement

- **Through the public API, with an injected sink.** Each configuration is driven with `TTSEngine(cfg, sink=TimingSink())`: no audio hardware, no playback, so the benchmark neither competes for the sound device nor lets playback (which blocks `feed` at 1×) distort the numbers. The sink records every chunk as `[arrival ms since the call, bytes, peak amplitude]`.
- **Everything raw, metrics derived afterwards.** A run's record is the full chunk timeline plus when the first sentence and the last word were sent (stream mode) and when the call returned. `derive_metrics` turns it into the metrics below at summary time; a new metric is one line in `METRICS` and one in that function, and applies to every past run through `--summarize`.
- **Metrics** (`METRICS`): `ttfa_ms` (call → first PCM chunk, the headline), `ttfv_ms` (call → first chunk with a sample above 500/32767, after leading silence), `after_first_sentence_ms` (stream only: first chunk relative to the first sentence's last word), `max_gap_ms` and `median_gap_ms` (stalls between chunks), `total_ms`, `audio_s`, `speed_x` (audio seconds per wall second after the first chunk, the real-time factor). Per entry: `load_s` (engine construction, downloads included on a first run) and `first_call_ttfa_ms` (the very first synthesis after construction, excluded from the statistics). Statistics (`STATS`): n, mean, median, stdev, min, p90, max.
- **Both input shapes, two texts.** `say` (complete text) and `stream` (the same text as a word-by-word async source). The source delivers words as fast as the engine's text bridge takes them (`--token-delay-ms 0`, the default), so stream-mode TTFA is the module's own streaming-path latency; a positive delay simulates an LLM and then TTFA includes the wait for the first sentence. `short` is one sentence, `medium` three, so sentence-grouped backends show their first-sentence dependence.
- **Warm-up then repeats.** Per mode × text, `--warmup` discarded runs (1) then `--runs` measured ones (5 by default; 20 for a bench meant to be kept), with `--pause-s` between runs (an entry may set its own `pause_s` for a rate limit).
- **One subprocess per entry.** A cold engine construction per entry, memory released between local models, a per-entry timeout (the load allowance plus every run's timeout) and a log file, so a hang or crash costs one entry.
- **Constant voice per provider**, only the model varies, so a provider's rows compare models and nothing else.

### The matrix

`matrix.json` lists the configurations: `{"entries": [{id, type, config, modes?, pause_s?, timeout_s?, requires_env?, enabled?, tags?, notes?}]}` where `config` is the `engine.module` block without `type`, relative paths resolve against the repository root, and `enabled: false` marks an opt-in entry (`--include-disabled`). Tags carry the selection vocabulary (`default` = the module's default model, `prev` = one version back kept for comparison, `api` / `local`, `deprecated`, `gated`). An entry is skipped, with the reason recorded, when its key variable (`config.api_key_env` or `requires_env`) is unset or its extra's library is not importable, mirroring the live tier's gate. Selection: `--only` / `--exclude` id globs, `--tags` / `--exclude-tags`.

The matrix covers every model reachable through each module, the module default first, one version back where it exists, and opt-in rows for older snapshots; the README's "Model catalog" records where each id was verified. Adding a backend to the library adds its rows here ([AGENTS.md](../AGENTS.md), "Adding a new TTS module").

### Runs and results

- **A run** is one invocation of `bench.py run`: `runs/<timestamp>/` holds its `env.json` (run id, start time, platform, Python, git SHA, library versions, settings, selection, texts), the plain `raw.jsonl` the workers append to, and `logs/<entry>.log`. Working data: the whole `runs/` tree is git-ignored. `--into <result>` merges the run into a result when it ends.
- **A result** is a benchmark worth keeping: `results/<YYYYMMDD-HHMM>_<name>/`, named after its first run's start time and the host (`bench.py new <name> --from-run <run>`). It holds `description.md` (hand-written: device, network, what the result covers, what affects comparability; created once from a template and never touched by the script), `runs.json` (every run merged so far, oldest first, with its environment and the entries it currently supplies), `raw.jsonl.gz` (the merged records, each tagged with its `run`) and the generated `results.csv`, `summary.csv` and `summary.md`. Committed as a whole.
- **Merging** (`bench.py merge <result> <run>...`, or `--into`): the result's current state plus the new runs are merged by one rule: for each entry, the **latest run that completed it** (a `done` record) supplies every record, so a newer run overrides an older measurement and an interrupted re-run never shadows a complete one; an entry no run completed keeps its latest attempt so the summary can say why it is missing. Latest means the run id (a timestamp) that sorts last. Re-merging a run replaces its records rather than duplicating them. Superseded measurements leave the result (they stay in `runs/` locally and in git history).
- **Two summaries.** `results.csv` is complete: one row per entry × mode × text with every statistic of every metric. `summary.csv` and `summary.md` are compact: **one row per entry, one number per column**, taken from the cells a reader decides on: TTFA p50 and p90 (`say`, short text: the headline and its tail), TTFA stream p50 (the short text as a token stream), TTFA long text p50 (`say`, three sentences: exposes a backend that does not stream), max gap and speed × (the three-sentence stream: pauses between sentences, real-time factor), load, first call, and the `run` that supplied the entry. Every record carries the module's `kind` (`hosted`, `local` or `fixture`, read from the registry when the record is written, [tts-module-interface.md](tts-module-interface.md) "Module kinds"), and both CSVs have a `kind` column. The markdown shows a **Local** table then a **Hosted** one, each grouped by module and fastest first within a module; the fixture modules (`tone`, `audiofile`, the harness floor) appear in `results.csv` only. Medians, because one stall skews a mean; whole milliseconds. The markdown adds the column meanings, the not-measured list with reasons, and a "Runs" table from the manifest. `bench.py summarize <dir>` rebuilds all three for a result, or for a single run for a quick look before merging.
- **Stop and resume.** Records are appended as they complete; `touch <run dir>/STOP` stops after the current entry; Ctrl-C or SIGTERM stops at once and records the entry as interrupted; `bench.py run --resume <run dir>` reruns the entries without a `done` record under the run's own settings and selection. A resumed run merged again replaces what it had contributed.
- **Never committed**: `runs/`, hand-placed model files under `models/`, the stdout logs of unattended passes (`*.log`).

### Where the findings live

`benchmarks/RESULTS.md` is the human reading of the benches: what was measured when, what stood out, which decisions it fed, what remains to measure. It cites results and runs by id and is updated in the same change as a run that changes a conclusion; a merge that changes what a result covers updates its `description.md` in the same change too. A spec that relies on a measurement quotes the number and the **result** id, never a run id: runs are git-ignored, so only a committed result can be re-checked (`runs.json` inside it names the run behind each entry). A number no result holds any more (superseded, or measured before the benchmark existed) is quoted with its date and conditions instead.

## Open questions

Deferrals only (design is settled):

1. **Cross-bench comparison.** A `--compare` listing the same entries across benches (a Mac bench against a Raspberry Pi bench) in one table. Until then each bench's summary is read on its own.
2. **Charts.** TTFA by module, say against stream, from `summary.csv`.
3. **A second benchmark kind** (quality, cost) would get its own subfolder under `benchmarks/` with its own README and, if it earns one, its own spec; this one stays at the root as the project's benchmark.
