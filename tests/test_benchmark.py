"""Unit tier for benchmarks/bench.py: the pure parts (metric derivation,
statistics, the CPU sampler and memory reading, matrix selection, the
measured-cells rule, run merging, results, summaries). No backend, no
subprocess, no network."""

from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_BENCH_PY = Path(__file__).resolve().parents[1] / "benchmarks" / "bench.py"
_spec = importlib.util.spec_from_file_location("latency_bench", _BENCH_PY)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
# Registered before executing: `@dataclass` resolves annotations through
# `sys.modules[cls.__module__]`, which must exist for a module loaded by path.
sys.modules["latency_bench"] = bench
_spec.loader.exec_module(bench)


# --- metrics -----------------------------------------------------------------


def test_derive_metrics_from_a_chunk_log():
    run = {
        "sample_rate": 24000,
        "end_ms": 320.0,
        "first_sentence_sent_ms": 40.0,
        # [arrival ms, bytes, peak]: the first chunk is leading silence.
        "chunk_log": [[100.0, 4800, 0], [150.0, 4800, 900], [300.0, 4800, 900]],
        # CPU: [window end ms, % of a core in the window], and the call's CPU seconds.
        "cpu_log": [[50.0, 120.0], [100.0, 250.0], [150.0, 40.0]],
        "cpu_s": 0.16,
    }
    m = bench.derive_metrics(run)
    assert m["cpu_peak_pct"] == 250.0  # the busiest window
    assert m["cpu_mean_pct"] == pytest.approx(100 * 0.16 / 0.32)  # CPU over the call
    assert m["ttfa_ms"] == 100.0
    assert m["ttfv_ms"] == 150.0  # first chunk with a sample above VOICE_THRESHOLD
    assert m["after_first_sentence_ms"] == 60.0
    assert m["max_gap_ms"] == 150.0
    assert m["median_gap_ms"] == 100.0
    assert m["total_ms"] == 320.0
    assert m["audio_s"] == pytest.approx(0.3)  # 3 × 4800 bytes of s16 at 24 kHz
    assert m["speed_x"] == pytest.approx(
        0.3 / 0.2
    )  # audio over wall time after the first chunk


def test_derive_metrics_before_cpu_was_recorded_is_none_for_cpu_only():
    # A record from a run that predates the sampler: the timing metrics are
    # unchanged, the CPU ones are None ("not measured"), not an error.
    run = {"sample_rate": 24000, "end_ms": 50.0, "chunk_log": [[20.0, 4800, 900]]}
    m = bench.derive_metrics(run)
    assert m["ttfa_ms"] == 20.0 and m["audio_s"] == pytest.approx(0.1)
    assert m["cpu_peak_pct"] is None and m["cpu_mean_pct"] is None
    # A sampler disabled (`--cpu-window-ms 0`) writes cpu_log None: average only.
    m = bench.derive_metrics({**run, "cpu_log": None, "cpu_s": 0.025})
    assert m["cpu_peak_pct"] is None and m["cpu_mean_pct"] == pytest.approx(50.0)


def test_derive_metrics_without_audio_is_all_none():
    m = bench.derive_metrics({"sample_rate": 24000, "end_ms": 5.0, "chunk_log": []})
    assert set(m) == set(bench.METRICS)
    assert all(v is None for v in m.values())


def test_cpu_sampler_logs_windows_in_percent_of_a_core():
    # Six windows expected; generous, so a loaded CI runner that wakes the
    # sampler late (one sample in 50 ms was seen on macOS) still gets two.
    sampler = bench.CpuSampler(window_s=0.025)
    sampler.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.15:  # busy: about one core, ~100 %
        pass
    sampler.stop()
    assert len(sampler.log) >= 2
    ends = [t for t, _ in sampler.log]
    assert ends == sorted(ends) and all(pct >= 0 for _, pct in sampler.log)
    assert max(pct for _, pct in sampler.log) > 20
    # Restarting starts a fresh log; stopping at once keeps a sub-window tail
    # only from 10 ms up, so a call shorter than that has no sample.
    sampler.start()
    sampler.stop()
    assert sampler.log == []


def test_peak_rss_mb_and_host_state_on_this_platform():
    rss = bench.peak_rss_mb()
    assert rss is None if os.name == "nt" else 1.0 < rss < 1e5  # MB of this process
    assert isinstance(bench.host_state(), dict)  # empty off a Raspberry Pi


def test_stats_p90_and_empty():
    s = bench._stats([float(v) for v in range(1, 11)])
    assert (s["n"], s["min"], s["max"], s["median"], s["p90"]) == (10, 1, 10, 5.5, 9)
    assert bench._stats([]) == {"n": 0}


# --- matrix selection --------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    base = {"only": None, "exclude": None, "tags": None, "exclude_tags": None}
    return argparse.Namespace(**{**base, **overrides})


_MATRIX = [
    {"id": "piper/low", "type": "piper", "config": {}, "tags": ["local"]},
    {"id": "piper/high", "type": "piper", "config": {}, "tags": ["local", "heavy"]},
    {"id": "tone", "type": "tone", "config": {}, "tags": ["default"]},
]


def test_select_entries_by_glob_and_tags():
    def ids(ns: argparse.Namespace) -> list[str]:
        return [e["id"] for e in bench.select_entries(_MATRIX, ns)]

    assert ids(_args(only=["piper/*"])) == ["piper/low", "piper/high"]
    assert ids(_args(exclude=["piper/*"])) == ["tone"]
    assert ids(_args(tags=["local"], exclude_tags=["heavy"])) == ["piper/low"]


def test_skip_reason(monkeypatch):
    disabled = {"id": "x", "type": "tone", "config": {}, "enabled": False}
    assert bench.skip_reason(disabled, include_disabled=False).startswith("disabled")
    assert bench.skip_reason(disabled, include_disabled=True) is None
    monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
    keyed = {"id": "y", "type": "tone", "config": {"api_key_env": "BENCH_TEST_KEY"}}
    assert bench.skip_reason(keyed, include_disabled=False) == "BENCH_TEST_KEY not set"
    monkeypatch.setitem(bench.REQUIRED_IMPORT, "tone", "no_such_library_for_bench")
    assert "not importable" in bench.skip_reason(
        {"id": "z", "type": "tone", "config": {}}, False
    )


async def test_token_stream_is_verbatim_and_marks_the_first_sentence():
    marks = bench.StreamMarks()
    text = "One two. Three four."
    pieces = [p async for p in bench.token_stream(text, 0.0, marks)]
    assert "".join(pieces) == text
    assert marks.first_sentence_sent is not None
    assert marks.last_piece_sent is not None
    assert marks.last_piece_sent >= marks.first_sentence_sent


def test_cli_subcommands_parse():
    args = bench.parse_args(
        ["run", "--only", "tone", "--runs", "2", "--into", "results/x"]
    )
    assert (args.command, args.only, args.runs, args.into) == (
        "run",
        ["tone"],
        2,
        "results/x",
    )
    assert bench.parse_args(["merge", "results/x", "runs/a", "runs/b"]).run_dirs == [
        "runs/a",
        "runs/b",
    ]
    assert bench.parse_args(["new", "pi", "--from-run", "runs/a"]).from_run == "runs/a"
    assert bench.parse_args(["summarize", "results/x"]).target == "results/x"
    assert args.cpu_window_ms == 50.0
    assert bench.parse_args(["run", "--cpu-window-ms", "0"]).cpu_window_ms == 0.0


# --- runs, merging and results -----------------------------------------------


# Fake module types of these tests: `m` is a hosted provider, `l` a local one.
_KIND = {"m": "hosted", "l": "local", "tone": "fixture"}


def _run_record(
    entry: str,
    mode: str,
    ttfa: float,
    seq: int = 1,
    ok: bool = True,
    cpu_pct: float | None = None,
) -> dict:
    """A run record as the worker writes it: the raw timeline plus the derived
    metrics appended. seq 0 is the warm-up (the first call after construction);
    `cpu_pct` adds a CPU log and CPU seconds, as a run with the sampler has."""
    if not ok:
        return {
            "entry": entry,
            "type": entry.split("/")[0],
            "kind": _KIND[entry.split("/")[0]],
            "record": "run",
            "mode": mode,
            "text": "short",
            "phase": "warmup" if seq == 0 else "measured",
            "iteration": seq,
            "seq": seq,
            "ok": False,
            "error": "TTSError: boom",
        }
    record = {
        "entry": entry,
        "type": entry.split("/")[0],
        "kind": _KIND[entry.split("/")[0]],
        "record": "run",
        "mode": mode,
        "text": "short",
        "phase": "warmup" if seq == 0 else "measured",
        "iteration": seq,
        "seq": seq,
        "ok": True,
        "sample_rate": 24000,
        "end_ms": ttfa + 100,
        "first_sentence_sent_ms": None,
        "last_piece_sent_ms": None,
        "drains": 1,
        "chunk_log": [[ttfa, 4800, 900], [ttfa + 50, 4800, 900]],
    }
    if cpu_pct is not None:
        record["cpu_log"] = [[ttfa, cpu_pct / 2], [ttfa + 50, cpu_pct]]
        record["cpu_s"] = cpu_pct / 100 * (ttfa + 100) / 1000 / 2
    record.update(bench.derive_metrics(record))
    return record


def _usage(entry: str, rss_mb: float) -> dict:
    return {
        "entry": entry,
        "type": entry.split("/")[0],
        "kind": _KIND[entry.split("/")[0]],
        "record": "usage",
        "rss_mb": rss_mb,
    }


def _load(entry: str) -> dict:
    return {
        "entry": entry,
        "type": entry.split("/")[0],
        "kind": _KIND[entry.split("/")[0]],
        "record": "load",
        "ok": True,
        "load_s": 0.5,
        "sample_rate": 24000,
    }


def _done(entry: str) -> dict:
    return {
        "entry": entry,
        "type": entry.split("/")[0],
        "kind": _KIND[entry.split("/")[0]],
        "record": "done",
        "seconds": 1.0,
    }


def _skip(entry: str) -> dict:
    return {
        "entry": entry,
        "type": entry.split("/")[0],
        "kind": _KIND[entry.split("/")[0]],
        "record": "skip",
        "reason": "KEY not set",
    }


def _write_run(
    runs_root: Path,
    run_id: str,
    records: list[dict],
    started: str = "2026-10-28T10:00:00+0100",
    measures: dict | None = None,
) -> Path:
    d = runs_root / run_id
    d.mkdir(parents=True)
    env = {
        "run": run_id,
        "started": started,
        "platform": "testOS",
        "machine": "arm64",
        "python": "3.13",
        "git_sha": "abc",
        "settings": {"runs": 1, "warmup": 1, "token_delay_ms": 0},
        "selection": {},
        "texts": bench.TEXTS,
    }
    if measures is not None:  # absent from a run made before resource usage
        env["measures"] = measures
    (d / "env.json").write_text(json.dumps(env))
    (d / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return d


def test_unmeasured_cells_finished_is_not_measured():
    """The merge fix: a worker whose every synthesis failed ran to the end but
    measured nothing, so the parent must not write the `done` record that
    would let the attempt supersede an older, complete measurement."""
    entry = {"id": "m/a", "type": "m", "config": {}}
    settings = {"modes": ["say", "stream"], "texts": ["short"]}
    all_failed = [_load("m/a")] + [
        _run_record("m/a", mode, 0.0, seq=i, ok=False)
        for mode in ("say", "stream")
        for i in range(3)
    ]
    assert bench.unmeasured_cells(all_failed, entry, settings) == [
        "say/short",
        "stream/short",
    ]
    # One successful measured run per cell is enough; a warm-up (seq 0) or a
    # failure is not; other entries' records do not count.
    partial = all_failed + [
        _run_record("m/a", "say", 10.0, seq=0),
        _run_record("m/a", "say", 11.0, seq=1),
        _run_record("m/b", "stream", 11.0, seq=1),
    ]
    assert bench.unmeasured_cells(partial, entry, settings) == ["stream/short"]
    complete = partial + [_run_record("m/a", "stream", 12.0, seq=4)]
    assert bench.unmeasured_cells(complete, entry, settings) == []
    # An entry restricted to one mode is complete without the other.
    say_only = {**entry, "modes": ["say"]}
    assert bench.unmeasured_cells(partial, say_only, settings) == []


def test_merge_runs_latest_completed_run_wins_and_aborts_never_shadow():
    runs = [
        (
            "r1",
            {},
            [_load("m/a"), _run_record("m/a", "say", 10.0), _done("m/a"), _skip("m/b")],
        ),
        (
            "r3",
            {},
            [
                _load("m/a"),
                _run_record("m/a", "say", 30.0),
                {
                    "entry": "m/a",
                    "type": "m",
                    "kind": "hosted",
                    "record": "abort",
                    "reason": "interrupted",
                },
            ],
        ),
        (
            "r2",
            {},
            [_load("m/a"), _run_record("m/a", "say", 20.0), _done("m/a"), _skip("m/b")],
        ),
    ]
    merged, source = bench.merge_runs(
        runs
    )  # order of the input does not matter: run ids do
    assert source == {
        "m/a": "r2",
        "m/b": "r2",
    }  # a: latest *completed*; b: latest attempt
    assert {r["run"] for r in merged} == {"r2"}
    assert [r["ttfa_ms"] for r in merged if r["record"] == "run"] == [20.0]


def test_merge_into_builds_a_result_then_a_newer_run_overrides(tmp_path: Path):
    runs_root, result = tmp_path / "runs", tmp_path / "results" / "20261028-1000_test"
    r1 = _write_run(
        runs_root,
        "20261028-100000",
        [
            _load("m/a"),
            _run_record("m/a", "say", 10.0, seq=0),
            _run_record("m/a", "say", 12.0),
            _done("m/a"),
            _load("m/c"),
            _run_record("m/c", "stream", 40.0),
            _done("m/c"),
        ],
    )
    bench.merge_into(result, [r1])

    assert (result / "description.md").exists()  # a template to fill in, written once
    (result / "description.md").write_text("# test\n\nmy notes\n")
    manifest = json.loads((result / "runs.json").read_text())
    assert [r["run"] for r in manifest["runs"]] == ["20261028-100000"]
    assert manifest["runs"][0]["supplies"] == ["m/a", "m/c"]
    assert "texts" not in manifest["runs"][0]  # the environment, not the whole env.json

    r2 = _write_run(
        runs_root,
        "20261028-110000",
        [
            _load("m/a"),
            _run_record("m/a", "say", 20.0, seq=0),
            _run_record("m/a", "say", 22.0, cpu_pct=200.0),
            _usage("m/a", 123.4),
            _done("m/a"),
            _skip("m/b"),
            _load("tone"),
            _run_record("tone", "say", 0.5),
            _done("tone"),
            _load("l/x"),
            _run_record("l/x", "say", 5.0, cpu_pct=350.0),
            _usage("l/x", 456.7),
            _done("l/x"),
        ],
        measures={"cpu_window_ms": 50, "rss": True, "gpu": None},
    )
    bench.merge_into(result, [r2])

    assert (
        result / "description.md"
    ).read_text() == "# test\n\nmy notes\n"  # never overwritten
    manifest = json.loads((result / "runs.json").read_text())
    assert {r["run"]: r["supplies"] for r in manifest["runs"]} == {
        "20261028-100000": ["m/c"],
        "20261028-110000": ["l/x", "m/a", "m/b", "tone"],
    }
    with gzip.open(result / "raw.jsonl.gz", "rt") as f:
        packed = [json.loads(line) for line in f]
    assert {(r["entry"], r["run"]) for r in packed} == {
        ("m/a", "20261028-110000"),
        ("m/b", "20261028-110000"),
        ("m/c", "20261028-100000"),
        ("tone", "20261028-110000"),
        ("l/x", "20261028-110000"),
    }

    md = (result / "summary.md").read_text()
    assert "| m/a | 1 | 0 |" in md and "| 20261028-110000 |" in md
    assert "| m/b | skipped: KEY not set | 20261028-110000 |" in md
    rows = {
        (r["entry"], r["mode"]): r
        for r in csv.DictReader((result / "results.csv").open())
    }
    assert rows[("m/a", "say")]["run"] == "20261028-110000"
    assert (
        float(rows[("m/a", "say")]["ttfa_ms__mean"]) == 22.0
    )  # the warm-up is not a measured run
    assert float(rows[("m/a", "say")]["first_call_ttfa_ms"]) == 20.0
    assert rows[("m/c", "stream")]["run"] == "20261028-100000"
    compact = {r["entry"]: r for r in csv.DictReader((result / "summary.csv").open())}
    # local first; by module, then matrix order (none of these is listed: by id)
    assert list(compact) == ["l/x", "m/a", "m/c"]
    assert compact["l/x"]["kind"] == "local" and compact["m/a"]["kind"] == "hosted"
    assert rows[("m/a", "say")]["kind"] == "hosted"  # results.csv carries it too
    assert (
        md.index("## Local")
        < md.index("| l/x |")
        < md.index("## Hosted")
        < md.index("| m/a |")
    )
    assert float(compact["m/a"]["ttfa_p50_ms"]) == 22.0
    assert compact["m/a"]["ttfa_stream_p50_ms"] == ""  # no stream cell for m/a
    assert (
        compact["m/c"]["ttfa_p50_ms"] == ""
        and compact["m/c"]["run"] == "20261028-100000"
    )
    assert "| TTFA p50 |" in md and "| m/c | 0 | 0 | — | — |" in md
    # Resource usage: the sampled run's peak memory and CPU, a dash and an
    # empty cell for the older run that predates them, and the Runs table
    # saying which run measured what.
    assert compact["m/a"]["peak_mb"] == "123" and compact["m/c"]["peak_mb"] == ""
    assert rows[("l/x", "say")]["peak_mb"] == "456.7"
    assert float(rows[("l/x", "say")]["cpu_peak_pct__median"]) == 350.0
    assert rows[("m/c", "stream")]["cpu_peak_pct__median"] == ""
    assert "| CPU peak p50 | CPU peak p90 | load s | first call | peak MB | run |" in md
    assert "| l/x | 1 | 0 | 5 | 5 | — | — | — | — | — | — | 0.50 | — | 457 |" in md
    assert "| 20261028-100000 |" in md and "| — |\n" in md  # r1: no measures
    assert "| cpu (50 ms windows), memory, no gpu |" in md  # r2
    assert (
        "tone" not in compact and "| tone |" not in md
    )  # fixtures stay in results.csv only
    assert any(r["entry"] == "tone" for r in rows.values())

    # Re-merging the same run replaces its records rather than duplicating them.
    bench.merge_into(result, [r2])
    assert len(json.loads((result / "runs.json").read_text())["runs"]) == 2
    bench.summarize(result)  # rebuilds from the packed state alone
    assert (result / "summary.md").read_text() == md


def test_new_result_is_dated_from_its_first_run(tmp_path: Path):
    run = _write_run(
        tmp_path / "runs",
        "20261027-145315",
        [_load("m/a"), _done("m/a")],
        started="2026-10-27T14:53:15+0100",
    )
    created = bench.new_result(tmp_path / "results", "mac", run)
    assert created == tmp_path / "results" / "20261027-1453_mac"
    text = (created / "description.md").read_text()
    assert text.startswith("# mac\n") and "testOS (arm64), Python 3.13" in text
    with pytest.raises(SystemExit):
        bench.new_result(tmp_path / "results", "mac", run)  # exists already


def test_summarize_a_single_run(tmp_path: Path):
    run = _write_run(
        tmp_path / "runs",
        "20261028-120000",
        [_load("m/a"), _run_record("m/a", "say", 15.0), _done("m/a")],
    )
    bench.summarize(run)
    assert "| m/a | 1 | 0 | 15 |" in (run / "summary.md").read_text()


def test_summary_follows_matrix_order_not_speed(tmp_path: Path):
    # Each configuration sits above its `+t<n>` caps whatever their speed; an
    # entry the matrix no longer lists comes after the listed ones, by id.
    ids = ["m/b", "m/b+t1", "m/b+t2", "m/a"]
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"entries": [{"id": e} for e in ids]}))
    ttfa = {"m/a": 5.0, "m/b": 30.0, "m/b+t1": 90.0, "m/b+t2": 10.0, "m/0-old": 1.0}
    records = [r for e, t in ttfa.items() for r in (_run_record(e, "say", t), _done(e))]
    run = _write_run(tmp_path / "runs", "20261028-120000", records)
    bench.summarize(run, matrix)
    compact = [r["entry"] for r in csv.DictReader((run / "summary.csv").open())]
    assert compact == ids + ["m/0-old"]
