"""Latency benchmark for every tts-engine module/model.

Drives each entry of ``matrix.json`` through the public ``TTSEngine`` API with
an injected timing sink (no audio hardware, no playback) and records, per run,
the arrival of every PCM chunk at the sink. Time to first audio, time to first
voice, stalls and real-time factor are derived from that timeline afterwards.

Each entry runs in its **own subprocess**: a cold engine construction per
entry (model load / client setup measured on its own), memory released between
local models, and a hung or crashing backend cannot take the run down.

Two trees:

- ``runs/<timestamp>/`` — every run as it happened: ``env.json``, the plain
  ``raw.jsonl`` the workers append to, ``logs/``. Working data, git-ignored.
- ``results/<YYYYMMDD-HHMM>_<name>/`` — a **result**: the runs merged into one
  state. ``description.md`` (hand-written: device, scope, notes), ``runs.json``
  (every run merged so far and its environment), ``raw.jsonl.gz`` (the winning
  records, each tagged with its run), ``results.csv`` (every statistic of every
  metric per entry × mode × text) and the compact ``summary.md`` / ``summary.csv``
  (one row per entry).
  Committed. Merging a newer run overrides an entry's older measurement: for
  each entry the latest run that completed it wins.

Usage (from the repo root; see README.md for the full option list):

    uv run python benchmarks/latency/bench.py list
    uv run python benchmarks/latency/bench.py run --only tone --runs 2
    zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run python benchmarks/latency/bench.py \
        run --into benchmarks/latency/results/20261027-1453_mac-m5-pro --runs 20 --tags api'
    uv run python benchmarks/latency/bench.py new mac-m5-pro --from-run benchmarks/latency/runs/<ts>
    uv run python benchmarks/latency/bench.py merge benchmarks/latency/results/<result> benchmarks/latency/runs/<ts>
    uv run python benchmarks/latency/bench.py summarize benchmarks/latency/results/<result>
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import fnmatch
import gzip
import importlib.metadata
import importlib.util
import itertools
import json
import logging
import os
import platform
import re
import signal
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("bench")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_MATRIX = HERE / "matrix.json"
DEFAULT_RUNS = HERE / "runs"
DEFAULT_RESULTS = HERE / "results"
RAW = "raw.jsonl"  # a run's plain, appended record file
RAW_GZ = "raw.jsonl.gz"  # a result's merged, packed record file
MANIFEST = "runs.json"
DESCRIPTION = "description.md"

# Benchmark texts. `short` is one sentence (the typical agent acknowledgement);
# `medium` is three sentences, so the text-stream mode shows the cost of
# sentence grouping (first audio only after the first sentence end) against
# the native-stream providers.
TEXTS: dict[str, str] = {
    "short": "Hello, this is a quick latency test of the speech engine.",
    "medium": (
        "The benchmark measures how long it takes before the first sound comes out. "
        "Each backend receives exactly the same text, so the numbers can be compared fairly. "
        "Longer answers like this one also show how streaming behaves across sentences."
    ),
}
MODES = ("say", "stream")

# module type -> importable library of its packaging extra (mirrors
# tests-e2e/support.py `_REQUIRED_IMPORT`).
REQUIRED_IMPORT: dict[str, str | tuple[str, ...]] = {
    "elevenlabs": "elevenlabs",
    "pocket": "pocket_tts",
    "gradium": "gradium",
    "phonon": "ptts",
    "mistral": "mistralai",
    "deepgram": "deepgram",
    "gemini": "google.genai",
    "cartesia": "cartesia",
    # The SDK alone is the openai-compatible extra; Realtime also needs websockets.
    "openai": ("openai", "websockets"),
    "openai-compatible": "openai",
    "qwen3": "qwentts_cpp",
    "kokoro": "kokoro_onnx",
    "piper": "piper",
}

# Distributions whose versions are recorded with the results.
DISTRIBUTIONS = (
    "tts-engine",
    "elevenlabs",
    "pocket-tts",
    "torch",
    "gradium",
    "ptts",
    "mistralai",
    "deepgram-sdk",
    "google-genai",
    "cartesia",
    "openai",
    "qwentts-cpp-python",
    "kokoro-onnx",
    "piper-tts",
    "onnxruntime",
)

# Fixture modules: the harness-overhead floor and the smoke-test target. They
# are measured like any entry (results.csv) but kept out of the compact summary,
# which compares real backends.
FIXTURE_TYPES = {"tone", "audiofile"}

# A chunk counts as "voiced" once a sample exceeds this amplitude (~ -36 dBFS):
# separates the first chunk (which may be leading silence) from the first
# audible sound.
VOICE_THRESHOLD = 500


# --------------------------------------------------------------------------
# Measurement (worker side)
# --------------------------------------------------------------------------


class TimingSink:
    """`AudioSink` that logs every chunk: arrival time, size and peak amplitude.

    The log is the raw material every metric is derived from afterwards
    (`derive_metrics`), so a metric not thought of today can still be computed
    from a past run. `feed` may run on a module's worker thread; calls never
    overlap, and `perf_counter` is process-wide, so stamps compare with the
    loop's.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.events: list[tuple[float, int, int]] = []  # (t, bytes, peak |sample|)
        self.drains = 0

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        now = time.perf_counter()
        samples = np.frombuffer(chunk[: len(chunk) & ~1], dtype=np.int16)
        peak = int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0
        self.events.append((now, len(chunk), peak))

    def drain(self) -> None:
        self.drains += 1


# Metric name -> (unit, definition). Every one is computed from a run's chunk
# log by `derive_metrics`, at summary time, so adding a metric is one line here
# and one in that function, and it applies to past runs as well.
METRICS: dict[str, tuple[str, str]] = {
    "ttfa_ms": ("ms", "call → first PCM chunk at the sink"),
    "ttfv_ms": (
        "ms",
        f"call → first chunk with a sample above {VOICE_THRESHOLD}/32767",
    ),
    "after_first_sentence_ms": (
        "ms",
        "first chunk − moment the first sentence was fully sent (stream only)",
    ),
    "max_gap_ms": ("ms", "longest wait between two consecutive chunks (a stall)"),
    "median_gap_ms": ("ms", "typical wait between two consecutive chunks"),
    "total_ms": ("ms", "call → return"),
    "audio_s": ("s", "audio produced"),
    "speed_x": ("×", "audio seconds per wall second after the first chunk"),
}

STATS = ("n", "mean", "median", "stdev", "min", "p90", "max")


def derive_metrics(run: dict) -> dict[str, float | None]:
    """Compute every `METRICS` value from a run record's chunk log."""
    log: list[list[float]] = run.get("chunk_log") or []
    if not log:
        return dict.fromkeys(METRICS)
    first, last = log[0][0], log[-1][0]
    voiced = next((t for t, _, peak in log if peak > VOICE_THRESHOLD), None)
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(log)]
    audio_s = sum(n for _, n, _ in log) / 2 / run["sample_rate"]
    gen_s = (last - first) / 1000
    sent = run.get("first_sentence_sent_ms")
    return {
        "ttfa_ms": first,
        "ttfv_ms": voiced,
        "after_first_sentence_ms": None if sent is None else first - sent,
        "max_gap_ms": max(gaps) if gaps else None,
        "median_gap_ms": statistics.median(gaps) if gaps else None,
        "total_ms": run["end_ms"],
        "audio_s": audio_s,
        # The capture sink never blocks, so this is the backend's own speed,
        # bounded by any server-side pacing.
        "speed_x": audio_s / gen_s if gen_s > 0 else None,
    }


@dataclass
class StreamMarks:
    first_sentence_sent: float | None = None
    last_piece_sent: float | None = None


async def token_stream(text: str, delay_s: float, marks: StreamMarks):
    """Yield `text` word by word (each word with its trailing space, so the
    pieces concatenate verbatim), `delay_s` apart. With `delay_s` 0 the words
    go out as fast as the engine's text bridge takes them, so what is measured
    is the module's streaming path, not the source's pacing; a positive delay
    simulates an LLM, and TTFA then includes the wait for the first sentence."""
    for piece in re.findall(r"\S+\s*", text):
        now = time.perf_counter()
        if marks.first_sentence_sent is None and re.search(r"[.!?]\s*$", piece):
            marks.first_sentence_sent = now
        marks.last_piece_sent = now
        yield piece
        if delay_s:
            await asyncio.sleep(delay_s)


async def measure_one(
    engine: Any, sink: TimingSink, mode: str, text: str, settings: dict
) -> dict:
    sink.reset()
    marks = StreamMarks()
    t0 = time.perf_counter()
    if mode == "say":
        call = engine.say(text)
    else:
        call = engine.say_stream(
            token_stream(text, settings["token_delay_ms"] / 1000.0, marks)
        )
    await asyncio.wait_for(call, timeout=settings["run_timeout_s"])
    t_end = time.perf_counter()

    def ms(t: float | None) -> float | None:
        return None if t is None else round((t - t0) * 1000, 2)

    # The raw record: every timestamp relative to the call, every chunk as
    # [arrival ms, bytes, peak |sample|]. The derived values are appended for
    # grep-ability only; `summarize` recomputes them from the log.
    run = {
        "sample_rate": engine.sample_rate,
        "end_ms": ms(t_end),
        "first_sentence_sent_ms": ms(marks.first_sentence_sent),
        "last_piece_sent_ms": ms(marks.last_piece_sent),
        "drains": sink.drains,
        "chunk_log": [[ms(t), n, peak] for t, n, peak in sink.events],
    }
    run.update(derive_metrics(run))
    return run


async def run_entry(entry: dict, settings: dict, out_path: Path) -> None:
    """Worker body: build the engine once, then run every mode x text x
    (warmup + runs), appending one JSON line per measurement to `out_path`."""
    from tts_engine import TTSEngine
    from tts_engine.config import TTSEngineConfig

    def emit(record: dict) -> None:
        record = {"entry": entry["id"], "type": entry["type"], **record}
        with out_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    sink = TimingSink()
    t0 = time.perf_counter()
    try:
        cfg = TTSEngineConfig.from_dict(
            {
                "module": {"type": entry["type"], **entry["config"]},
                "player": {"device": None},
            },
            base_dir=REPO_ROOT,
        )
        engine = TTSEngine(cfg, sink=sink)
    except Exception as exc:
        emit({"kind": "load", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise
    emit(
        {
            "kind": "load",
            "ok": True,
            "load_s": time.perf_counter() - t0,
            "sample_rate": engine.sample_rate,
        }
    )

    modes = [m for m in settings["modes"] if m in entry.get("modes", MODES)]
    pause = float(entry.get("pause_s", settings["pause_s"]))
    seq = 0
    async with engine:
        for mode in modes:
            for text_id in settings["texts"]:
                failures = 0
                for i in range(settings["warmup"] + settings["runs"]):
                    phase = "warmup" if i < settings["warmup"] else "measured"
                    base = {
                        "kind": "run",
                        "mode": mode,
                        "text": text_id,
                        "phase": phase,
                        "iteration": i,
                        "seq": seq,
                    }
                    try:
                        m = await measure_one(
                            engine, sink, mode, TEXTS[text_id], settings
                        )
                        emit({**base, "ok": True, **m})
                        failures = 0
                    except Exception as exc:  # noqa: BLE001 — TimeoutError, TTSError, ...; record and go on
                        emit(
                            {
                                **base,
                                "ok": False,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        failures += 1
                        log.warning(
                            "%s %s/%s #%d failed: %s",
                            entry["id"],
                            mode,
                            text_id,
                            i,
                            exc,
                        )
                        if failures >= settings["max_consecutive_failures"]:
                            log.error(
                                "%s %s/%s: giving up after %d failures",
                                entry["id"],
                                mode,
                                text_id,
                                failures,
                            )
                            break
                    seq += 1
                    if pause:
                        await asyncio.sleep(pause)


# --------------------------------------------------------------------------
# Orchestration (parent side)
# --------------------------------------------------------------------------


def _importable(lib: str) -> bool:
    try:
        return importlib.util.find_spec(lib) is not None
    except ModuleNotFoundError:
        return False


def skip_reason(entry: dict, include_disabled: bool) -> str | None:
    if not entry.get("enabled", True) and not include_disabled:
        return "disabled (use --include-disabled)"
    env_names = list(entry.get("requires_env", []))
    if key_env := entry["config"].get("api_key_env"):
        env_names.insert(0, key_env)
    for name in env_names:
        if not os.environ.get(name):
            return f"{name} not set"
    libs = REQUIRED_IMPORT.get(entry["type"], ())
    for lib in (libs,) if isinstance(libs, str) else libs:
        if not _importable(lib):
            return f"{lib!r} not importable (uv sync --extra {entry['type']})"
    return None


def select_entries(matrix: list[dict], args: argparse.Namespace) -> list[dict]:
    selected = []
    for e in matrix:
        if args.only and not any(fnmatch.fnmatch(e["id"], pat) for pat in args.only):
            continue
        if args.exclude and any(fnmatch.fnmatch(e["id"], pat) for pat in args.exclude):
            continue
        if args.tags and not set(args.tags) & set(e.get("tags", [])):
            continue
        if args.exclude_tags and set(args.exclude_tags) & set(e.get("tags", [])):
            continue
        selected.append(e)
    return selected


def environment_info() -> dict:
    versions = {}
    for dist in DISTRIBUTIONS:
        try:
            versions[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            pass
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — git missing or not a repo
        sha = None
    return {
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "git_sha": sha,
        "versions": versions,
    }


# Touch this file in the run directory to stop cleanly: the running entry
# finishes, nothing is lost, and the summary is written. For an immediate stop,
# Ctrl-C or `kill <pid>` (SIGTERM): the current entry is dropped and recorded as
# interrupted; the entries already done keep their records.
STOP_FILE = "STOP"


def _record(raw: Path, entry: dict, kind: str, **fields: Any) -> None:
    with raw.open("a") as f:
        f.write(
            json.dumps(
                {"entry": entry["id"], "type": entry["type"], "kind": kind, **fields}
            )
            + "\n"
        )


def read_records(directory: Path) -> list[dict]:
    """The records in a directory: a run's plain `raw.jsonl`, or a result's
    packed `raw.jsonl.gz`."""
    plain, packed = directory / RAW, directory / RAW_GZ
    if plain.exists():
        text = plain.read_text()
    elif packed.exists():
        with gzip.open(packed, "rt") as f:
            text = f.read()
    else:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _completed_entries(run_dir: Path) -> set[str]:
    """Ids whose worker ran to the end (a `done` record) in a previous session."""
    return {r["entry"] for r in read_records(run_dir) if r["kind"] == "done"}


def _drop_entries(raw: Path, ids: set[str]) -> None:
    """Rewrite raw.jsonl without the records of `ids`: they are about to run
    again from scratch, and a half-measured attempt must not be counted next
    to the full one."""
    if not raw.exists():
        return
    kept = [
        line
        for line in raw.read_text().splitlines()
        if line.strip() and json.loads(line)["entry"] not in ids
    ]
    raw.write_text("".join(line + "\n" for line in kept))


def _raise_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def run_all(args: argparse.Namespace, entries: list[dict]) -> Path:
    if args.resume:
        # Same directory, same settings (so the resumed entries are comparable
        # with the finished ones); entries without a `done` record run again.
        out_dir = Path(args.resume)
        settings = json.loads((out_dir / "env.json").read_text())["settings"]
        done = _completed_entries(out_dir)
        entries = [e for e in entries if e["id"] not in done]
        print(
            f"Resuming {out_dir}: {len(done)} entries done, {len(entries)} to run",
            flush=True,
        )
        _drop_entries(out_dir / RAW, {e["id"] for e in entries})
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        out_dir = Path(args.runs_dir) / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        settings = {
            "runs": args.runs,
            "warmup": args.warmup,
            "modes": args.modes,
            "texts": args.texts,
            "token_delay_ms": args.token_delay_ms,
            "pause_s": args.pause_s,
            "run_timeout_s": args.run_timeout_s,
            "entry_timeout_s": args.entry_timeout_s,
            "max_consecutive_failures": args.max_consecutive_failures,
        }
        selection = {
            k: getattr(args, k)
            for k in ("only", "exclude", "tags", "exclude_tags", "include_disabled")
        }
        (out_dir / "env.json").write_text(
            json.dumps(
                {
                    "run": run_id,
                    **environment_info(),
                    "settings": settings,
                    "selection": selection,
                    "texts": TEXTS,
                },
                indent=2,
            )
        )
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    raw = out_dir / RAW
    stop_file = out_dir / STOP_FILE
    stop_file.unlink(missing_ok=True)
    print(f"Run: {out_dir}  (touch {stop_file} to stop after the current entry)")

    stopped = False
    for n, entry in enumerate(entries, 1):
        prefix = f"[{n}/{len(entries)}] {entry['id']}"
        if stop_file.exists():
            print(f"{prefix}: STOP file found, stopping here", flush=True)
            stopped = True
            break
        reason = skip_reason(entry, args.include_disabled)
        if reason:
            print(f"{prefix}: skipped — {reason}", flush=True)
            _record(raw, entry, "skip", reason=reason)
            continue
        print(f"{prefix}: running …", flush=True)
        log_path = out_dir / "logs" / (entry["id"].replace("/", "__") + ".log")
        # Budget: the load allowance (model download included) plus the worst
        # case of every run timing out, so it follows the run count. A hang
        # inside a run is already cut by run_timeout_s; this is the backstop.
        n_runs = (
            len([m for m in settings["modes"] if m in entry.get("modes", MODES)])
            * len(settings["texts"])
            * (settings["warmup"] + settings["runs"])
        )
        pause = float(entry.get("pause_s", settings["pause_s"]))
        timeout = entry.get("timeout_s", settings["entry_timeout_s"]) + n_runs * (
            settings["run_timeout_s"] + pause
        )
        cmd = [
            sys.executable,
            __file__,
            "worker",
            "--entry-json",
            json.dumps(entry),
            "--settings-json",
            json.dumps(settings),
            "--raw",
            str(raw),
        ]
        t0 = time.perf_counter()
        try:
            with log_path.open("w") as logf:
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=REPO_ROOT,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        timeout=timeout,
                        check=False,
                    )
                    status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
                except subprocess.TimeoutExpired:
                    status = f"timeout after {timeout}s"
        except KeyboardInterrupt:
            # subprocess.run has already killed the worker. Its finished runs
            # stay in raw.jsonl; the entry is marked so --resume redoes it.
            _record(raw, entry, "abort", reason="interrupted")
            print(f"{prefix}: interrupted", flush=True)
            stopped = True
            break
        if status == "ok":
            _record(raw, entry, "done", seconds=round(time.perf_counter() - t0, 1))
        else:
            _record(raw, entry, "abort", reason=status)
        print(
            f"{prefix}: {status} ({time.perf_counter() - t0:.0f}s, log {log_path.name})",
            flush=True,
        )

    incomplete = stopped or len(_completed_entries(out_dir)) < len(
        [e for e in entries if not skip_reason(e, args.include_disabled)]
    )
    summarize(out_dir)
    if args.into:
        merge_into(Path(args.into), [out_dir])
    if incomplete:
        print(f"Incomplete. Resume with: bench.py run --resume {out_dir}")
    return out_dir


# --------------------------------------------------------------------------
# Results: merging runs
# --------------------------------------------------------------------------


Run = tuple[str, dict, list[dict]]  # (run id, env, records)


def _run_env(env: dict) -> dict:
    """What a result keeps of a run's environment (everything but the texts)."""
    return {k: v for k, v in env.items() if k != "texts"}


def load_run(run_dir: Path) -> Run:
    env_path = run_dir / "env.json"
    env = json.loads(env_path.read_text()) if env_path.exists() else {}
    return env.get("run", run_dir.name), _run_env(env), read_records(run_dir)


def load_result(result_dir: Path) -> list[Run]:
    """A result's current state as runs: the manifest's environments, the
    packed records grouped by the `run` tag each carries."""
    manifest_path = result_dir / MANIFEST
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text())
    by_run: dict[str, list[dict]] = defaultdict(list)
    for r in read_records(result_dir):
        by_run[r["run"]].append(r)
    return [(run["run"], run, by_run.get(run["run"], [])) for run in manifest["runs"]]


def merge_runs(runs: list[Run]) -> tuple[list[dict], dict[str, str]]:
    """Merge runs into one state: for each entry, the **latest run that
    completed it** (a `done` record) supplies every record, so a re-run
    supersedes an older measurement and an interrupted re-run never shadows a
    complete one. An entry no run completed keeps its latest attempt (a skip,
    an abort, a load failure), so the summary can say why it is missing.
    Latest = the run id (a timestamp) that sorts last. Returns the merged
    records, each tagged with its `run`, and entry → run."""
    ordered = sorted(runs, key=lambda run: run[0])
    completed: dict[str, str] = {}
    attempted: dict[str, str] = {}
    for run_id, _env, records in ordered:
        for r in records:
            attempted[r["entry"]] = run_id
            if r["kind"] == "done":
                completed[r["entry"]] = run_id
    source = {**attempted, **completed}
    merged = [
        {**r, "run": run_id}
        for run_id, _env, records in ordered
        for r in records
        if source.get(r["entry"]) == run_id
    ]
    return merged, source


def merge_into(result_dir: Path, run_dirs: list[Path]) -> None:
    """Merge `run_dirs` into the result: rewrite its packed records, its
    manifest (every run merged so far, oldest first, with the entries each
    currently supplies) and its summaries. A run already merged is replaced by
    the directory's current content (a resumed run, say)."""
    known = {
        run_id: (run_id, env, records)
        for run_id, env, records in load_result(result_dir)
    }
    for d in run_dirs:
        run_id, env, records = load_run(d)
        if not records:
            raise SystemExit(f"{d}: no records to merge")
        known[run_id] = (run_id, env, records)
    runs = sorted(known.values(), key=lambda run: run[0])
    merged, source = merge_runs(runs)

    result_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(result_dir / RAW_GZ, "wt") as f:
        f.write("".join(json.dumps(r) + "\n" for r in merged))
    manifest = {
        "result": result_dir.name,
        "runs": [
            {
                **env,
                "run": run_id,
                "supplies": sorted(e for e, r in source.items() if r == run_id),
            }
            for run_id, env, _records in runs
        ],
    }
    (result_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    if not (result_dir / DESCRIPTION).exists():
        write_description(result_dir, runs[0][1])
    print(
        f"Merged {len(run_dirs)} run(s) into {result_dir}: {len(runs)} runs, {len(source)} entries"
    )
    summarize(result_dir)


def new_result(results_root: Path, name: str, from_run: Path | None) -> Path:
    """Create `results/<YYYYMMDD-HHMM>_<name>/` with a description to fill in,
    dated from the first run when one is given, else from now."""
    env = load_run(from_run)[1] if from_run else {}
    started = env.get("started")
    stamp = (
        time.strftime("%Y%m%d-%H%M", time.strptime(started[:16], "%Y-%m-%dT%H:%M"))
        if started
        else time.strftime("%Y%m%d-%H%M")
    )
    result_dir = results_root / f"{stamp}_{name}"
    if result_dir.exists():
        raise SystemExit(f"{result_dir} already exists")
    result_dir.mkdir(parents=True)
    write_description(result_dir, env, name)
    print(f"Created {result_dir}: fill in {DESCRIPTION}, then merge runs into it")
    return result_dir


def write_description(result_dir: Path, env: dict, name: str | None = None) -> None:
    name = name or result_dir.name.split("_", 1)[-1]
    (result_dir / DESCRIPTION).write_text(
        f"# {name}\n\n"
        f"**Device:** {env.get('platform', '<platform>')} ({env.get('machine', '<machine>')}), "
        f"Python {env.get('python', '<version>')}\n\n"
        "**Network:** <connection and location, for the API rows>\n\n"
        "**What:** <which modules/models this result covers, and why>\n\n"
        "**Notes:** <anything that affects comparability: credits, throttling, "
        "concurrent load, module changes between runs>\n"
    )


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "mean": statistics.fmean(s),
        "median": statistics.median(s),
        "stdev": statistics.stdev(s) if len(s) > 1 else 0.0,
        "min": s[0],
        "max": s[-1],
        "p90": s[min(len(s) - 1, round(0.9 * (len(s) - 1)))],
    }


def _cell(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


# The compact summary: one row per entry, one number per column, taken from the
# cells a reader decides on. (csv column, label, mode, text, results.csv key,
# digits, definition). Everything else — means, max, stdev, TTFV, the gaps of
# every cell — stays in results.csv.
SUMMARY_COLUMNS: list[tuple[str, str, str, str, str, int, str]] = [
    (
        "ttfa_p50_ms",
        "TTFA p50",
        "say",
        "short",
        "ttfa_ms__median",
        0,
        "median time to first audio, `say()` on the short text: the headline",
    ),
    (
        "ttfa_p90_ms",
        "TTFA p90",
        "say",
        "short",
        "ttfa_ms__p90",
        0,
        "its tail: 9 runs in 10 answered within this",
    ),
    (
        "ttfa_stream_p50_ms",
        "TTFA stream p50",
        "stream",
        "short",
        "ttfa_ms__median",
        0,
        (
            "the same short text fed as a token stream (`say_stream()`): "
            "the streaming path's own cost or gain"
        ),
    ),
    (
        "ttfa_long_text_p50_ms",
        "TTFA long text p50",
        "say",
        "medium",
        "ttfa_ms__median",
        0,
        (
            "`say()` on the three-sentence text: grows with the text for a backend "
            "that does not really stream"
        ),
    ),
    (
        "max_gap_ms",
        "max gap",
        "stream",
        "medium",
        "max_gap_ms__median",
        0,
        (
            "median over runs of the longest silence between two audio chunks, "
            "three-sentence stream: the pause between sentences; longer than the "
            "audio queued in the player means a stutter"
        ),
    ),
    (
        "speed_x",
        "speed ×",
        "stream",
        "medium",
        "speed_x__median",
        1,
        (
            "audio seconds produced per wall second after the first chunk, "
            "three-sentence stream: below 1 playback starves"
        ),
    ),
]
SUMMARY_FIXED = [
    (
        "load_s",
        "load s",
        2,
        "engine construction: client or socket setup, model load (download included on a first run)",
    ),
    (
        "first_call_ttfa_ms",
        "first call",
        0,
        "TTFA of the very first synthesis after construction (cold connection, kernels); not in the statistics",
    ),
]


def _settings_line(env: dict) -> str:
    s = env.get("settings", {})
    sel = {k: v for k, v in env.get("selection", {}).items() if v}
    return (
        f"{s.get('warmup')} warm-up + {s.get('runs')} measured per cell, "
        f"token delay {s.get('token_delay_ms')} ms"
        + (f"; selection {json.dumps(sel)}" if sel else "")
    )


def _rounded(value: float | None, digits: int) -> float | int | None:
    """Summary values at the precision a reader needs: whole milliseconds, one
    decimal of speed, two of seconds (results.csv keeps every digit)."""
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["entry"])
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})


def summarize(target: Path) -> None:
    """Write into `target` (a result directory, its merged state, or a single
    run directory): `results.csv`, every statistic of every metric for every
    entry × mode × text; `summary.csv` and `summary.md`, one row per entry with
    the `SUMMARY_COLUMNS`."""
    runs = load_result(target) if (target / MANIFEST).exists() else [load_run(target)]
    records, source = merge_runs(runs)
    load: dict[str, dict] = {}
    status: dict[str, str] = {}
    first_call: dict[str, dict] = {}
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    errors: dict[tuple[str, str, str], int] = defaultdict(int)
    types: dict[str, str] = {}

    for r in records:
        eid = r["entry"]
        types[eid] = r["type"]
        kind = r["kind"]
        if kind == "skip":
            status[eid] = f"skipped: {r['reason']}"
        elif kind == "abort":
            status.setdefault(eid, f"aborted: {r['reason']}")
        elif kind == "load":
            load[eid] = r
            if not r["ok"]:
                status[eid] = f"load failed: {r['error']}"
        elif kind == "run":
            key = (eid, r["mode"], r["text"])
            if r.get("seq") == 0 and r["ok"]:
                first_call[eid] = r
            if not r["ok"]:
                errors[key] += 1
            elif r["phase"] == "measured":
                groups[key].append(r)

    # results.csv: one row per cell, every statistic of every metric.
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key in sorted(set(groups) | set(errors)):
        eid, mode, text = key
        cell_runs = groups.get(key, [])
        metrics = [derive_metrics(r) for r in cell_runs]
        fc = first_call.get(eid)
        row: dict[str, Any] = {
            "entry": eid,
            "type": types[eid],
            "mode": mode,
            "text": text,
            "run": source.get(eid),
            "n": len(cell_runs),
            "errors": errors.get(key, 0),
            "load_s": load[eid]["load_s"] if load.get(eid, {}).get("ok") else None,
            "first_call_ttfa_ms": derive_metrics(fc)["ttfa_ms"] if fc else None,
        }
        for name in METRICS:
            st = _stats([v for m in metrics if (v := m[name]) is not None])
            for stat in STATS:
                row[f"{name}__{stat}"] = st.get(stat)
        cells[key] = row
    _write_csv(target / "results.csv", list(cells.values()))

    # summary.csv: one row per entry.
    summary: list[dict[str, Any]] = []
    for eid in sorted({e for e, _, _ in cells}):
        if types[eid] in FIXTURE_TYPES:
            continue
        own = [c for k, c in cells.items() if k[0] == eid]
        headline = cells.get((eid, "say", "short"))
        row = {
            "entry": eid,
            "type": types[eid],
            "n": headline["n"] if headline else 0,
            "errors": sum(c["errors"] for c in own),
        }
        for column, _label, mode, text, key, digits, _desc in SUMMARY_COLUMNS:
            cell = cells.get((eid, mode, text))
            row[column] = _rounded(cell[key], digits) if cell else None
        for column, _label, digits, _desc in SUMMARY_FIXED:
            row[column] = _rounded(own[0][column], digits)
        row["run"] = source.get(eid)
        summary.append(row)
    # Grouped by module, fastest first within a module.
    summary.sort(
        key=lambda r: (r["type"], r["ttfa_p50_ms"] is None, r["ttfa_p50_ms"] or 0)
    )
    _write_csv(target / "summary.csv", summary)

    # summary.md: the same table, readable.
    labels = [c[1] for c in SUMMARY_COLUMNS] + [c[1] for c in SUMMARY_FIXED]
    lines = [
        f"# Latency benchmark — `{target.name}`",
        "",
        (
            "Generated by `bench.py`; what was benchmarked and on what is in "
            f"`{DESCRIPTION}`. One row per entry, grouped by module and fastest first "
            "within a module; the fixture modules (tone, audiofile) are left out, "
            "see `results.csv`; times in ms, "
            "statistics over the measured (warm) runs; `n` = measured runs behind "
            "the headline cell, `err` = failed runs over every cell. For each entry "
            "the latest run that completed it supplies the numbers (`run`). Every "
            "statistic of every metric for every mode × text is in `results.csv`; "
            f"the per-chunk timeline of every run is in `{RAW_GZ}`."
        ),
        "",
        "| entry | n | err | " + " | ".join(labels) + " | run |",
        "|---|---:|---:|" + "---:|" * len(labels) + "---|",
    ]
    for r in summary:
        values = [
            _cell(r[column], digits)
            for column, _label, _mode, _text, _key, digits, _desc in SUMMARY_COLUMNS
        ] + [
            _cell(r[column], digits) for column, _label, digits, _desc in SUMMARY_FIXED
        ]
        lines.append(
            f"| {r['entry']} | {r['n']} | {r['errors']} | "
            + " | ".join(values)
            + f" | {r['run']} |"
        )
    lines += ["", "| column | meaning |", "|---|---|"]
    lines += [
        f"| {label} | {desc} |" for _c, label, _m, _t, _k, _d, desc in SUMMARY_COLUMNS
    ]
    lines += [f"| {label} | {desc} |" for _c, label, _d, desc in SUMMARY_FIXED]
    lines.append("")
    if status:
        lines += ["## Not measured", "", "| entry | status | run |", "|---|---|---|"]
        lines += [
            f"| {eid} | {st} | {source.get(eid)} |"
            for eid, st in sorted(status.items())
        ]
        lines.append("")
    lines += [
        "## Runs",
        "",
        "| run | started | platform | Python | git | supplies | settings |",
        "|---|---|---|---|---|---:|---|",
    ]
    for run_id, env, _records in sorted(runs, key=lambda run: run[0]):
        supplied = sum(1 for e, r in source.items() if r == run_id)
        lines.append(
            f"| {run_id} | {env.get('started', '?')} | {env.get('platform', '?')} "
            f"({env.get('machine', '?')}) | {env.get('python', '?')} | "
            f"{env.get('git_sha', '?')} | {supplied} | {_settings_line(env)} |"
        )
    lines.append("")
    (target / "summary.md").write_text("\n".join(lines))
    print(f"Summary written to {target}: results.csv, summary.csv, summary.md")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _add_selection(p: argparse.ArgumentParser) -> None:
    p.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="matrix JSON file")
    p.add_argument(
        "--only",
        nargs="+",
        metavar="GLOB",
        help="entry ids to run (fnmatch globs, e.g. 'cartesia/*' 'piper/*-low')",
    )
    p.add_argument(
        "--exclude",
        nargs="+",
        metavar="GLOB",
        help="entry ids to leave out (fnmatch globs, e.g. 'gemini/*')",
    )
    p.add_argument("--tags", nargs="+", help="run entries carrying any of these tags")
    p.add_argument(
        "--exclude-tags", nargs="+", help="drop entries carrying any of these tags"
    )
    p.add_argument(
        "--include-disabled",
        action="store_true",
        help="also run entries marked enabled=false",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)

    lst = sub.add_parser(
        "list",
        help="list the selected entries and whether each would run; runs nothing",
    )
    _add_selection(lst)
    lst.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))

    run = sub.add_parser(
        "run", help="measure the selected entries into runs/<timestamp>"
    )
    _add_selection(run)
    run.add_argument(
        "--runs", type=int, default=5, help="measured runs per mode/text (default 5)"
    )
    run.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="discarded warm-up runs per mode/text (default 1)",
    )
    run.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    run.add_argument("--texts", nargs="+", choices=list(TEXTS), default=list(TEXTS))
    run.add_argument(
        "--token-delay-ms",
        type=float,
        default=0.0,
        help="delay between words in stream mode. 0 (default) delivers the text as "
        "fast as the engine takes it, so stream TTFA is the module's own; "
        "e.g. 30 to simulate an LLM, where TTFA then includes the pacing",
    )
    run.add_argument(
        "--pause-s",
        type=float,
        default=0.5,
        help="sleep between runs unless the entry sets pause_s (default 0.5)",
    )
    run.add_argument(
        "--run-timeout-s",
        type=float,
        default=120.0,
        help="bound on one synthesis (default 120)",
    )
    run.add_argument(
        "--entry-timeout-s",
        type=float,
        default=600.0,
        help="load/download allowance per entry; each run adds run_timeout_s + pause "
        "to the subprocess budget (default 600)",
    )
    run.add_argument("--max-consecutive-failures", type=int, default=3)
    run.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS),
        help="where runs land (default benchmarks/latency/runs, git-ignored)",
    )
    run.add_argument(
        "--into",
        metavar="RESULT_DIR",
        help="merge the run into this result directory when it ends "
        "(see `new` and `merge`)",
    )
    run.add_argument(
        "--resume",
        metavar="RUN_DIR",
        help="continue an interrupted run in RUN_DIR: same settings and selection, "
        "entries without a done record run again (a partial one from scratch)",
    )

    new = sub.add_parser(
        "new",
        help="create results/<YYYYMMDD-HHMM>_<name>/ with a description to fill in",
    )
    new.add_argument(
        "name", help="the result's name, usually the host (e.g. mac-m5-pro)"
    )
    new.add_argument(
        "--from-run",
        metavar="RUN_DIR",
        help="date the folder from this run and pre-fill the description from its environment",
    )
    new.add_argument(
        "--results-dir", default=str(DEFAULT_RESULTS), help=argparse.SUPPRESS
    )

    merge = sub.add_parser(
        "merge",
        help="merge runs into a result (latest completed measurement of each entry wins)",
    )
    merge.add_argument("result", metavar="RESULT_DIR")
    merge.add_argument("run_dirs", metavar="RUN_DIR", nargs="+")

    summ = sub.add_parser(
        "summarize", help="rebuild summary.md/csv of a result (or of a single run)"
    )
    summ.add_argument("target", metavar="DIR")

    # Internal: worker subprocess mode.
    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--entry-json", required=True)
    worker.add_argument("--settings-json", required=True)
    worker.add_argument("--raw", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.command == "worker":
        asyncio.run(
            run_entry(
                json.loads(args.entry_json),
                json.loads(args.settings_json),
                Path(args.raw),
            )
        )
        return 0
    if args.command == "summarize":
        summarize(Path(args.target))
        return 0
    if args.command == "new":
        new_result(
            Path(args.results_dir),
            args.name,
            Path(args.from_run) if args.from_run else None,
        )
        return 0
    if args.command == "merge":
        merge_into(Path(args.result), [Path(d) for d in args.run_dirs])
        return 0

    if args.command == "run":
        # `kill <pid>` behaves like Ctrl-C: the worker is killed, the entry
        # marked interrupted, the summary written. The worker keeps the default
        # handler.
        signal.signal(signal.SIGTERM, _raise_interrupt)
        if args.resume:
            env = json.loads((Path(args.resume) / "env.json").read_text())
            for key, value in env.get("selection", {}).items():
                setattr(args, key, value)

    matrix = json.loads(Path(args.matrix).read_text())["entries"]
    entries = select_entries(matrix, args)
    if not entries:
        print("No matrix entry matches the selection.")
        return 1

    if args.command == "list":
        for e in entries:
            reason = skip_reason(e, args.include_disabled)
            modes = ",".join(m for m in args.modes if m in e.get("modes", MODES))
            print(
                f"{'SKIP' if reason else 'RUN ':4}  {e['id']:<40} modes={modes:<11} "
                f"tags={','.join(e.get('tags', []))}"
                + (f"  — {reason}" if reason else "")
            )
        return 0

    out_dir = run_all(args, entries)
    print(f"Done: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
