# Benchmark playback metrics

**Status:** Done

Implements the settled metrics in [specs/benchmark.md](../specs/benchmark.md) ("Metrics", "Two summaries"). Adds predicted first voice, time to continuous audio and a whole-call real-time factor, all derived from the existing chunk timelines, so both results regenerate without synthesis. It leaves out recording the exact sample of voice onset, which needs new runs.

## Scope

- `benchmarks/bench.py` — `first_voice_ms`, `continuous_ms`, `realtime_x` in `METRICS` and `derive_metrics`; `first voice p50` and `continuous p50` added to the summary and `real-time ×` replacing `speed ×` there; `speed_x` kept in `results.csv`, its description corrected
- `tests/test_benchmark.py` — hand-calculated timelines for the new metrics
- `specs/benchmark.md`, `specs/_index.md`, `benchmarks/README.md` — the metric and column definitions
- `benchmarks/results/*/results.csv`, `summary.csv`, `summary.md` — regenerated
- `benchmarks/results/20261029-1124_pi4-1500-nodaemon/findings.md` — its hand-written calculation replaced by the column

## Steps

1. Add the two timeline functions and the three metrics, leaving `speed_x` unchanged.
2. Add the summary columns and correct the `max gap` and `speed_x` wording.
3. Add the tests: silence before voice, a late whole-sentence burst, a one-chunk output, no audio, a gap covered by queued audio, a stream above real time on average that stalls early.
4. Run `bench.py summarize` on both results.
5. Update the spec, the README and the Pi findings.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks tools`, `uv run pyright`, `uv run pytest`. Regenerated values match an independent calculation from the raw records: `deepgram/flux-haley-en` first voice 1116 ms, `piper/en_US-ljspeech-medium` 69.05× on the Mac; `pocket/english_2m_2026-10+quantize` continuous 194 ms and 1.06× on the Pi.
