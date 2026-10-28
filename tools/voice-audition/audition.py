"""Voice audition: hear candidate voices side by side, measure them, pick some.

An audition file (JSON) holds one sentence and a list of candidates, each a
complete ``engine.module`` block. Three commands:

- ``new`` builds an audition file from the backends' own catalogs
  (``tts_engine.list_voices``), filtered by gender, language and text;
- ``render`` synthesizes every candidate through ``TTSEngine``: one warm-up,
  then ``--runs`` timed calls whose time to first audio is recorded, the last
  run's audio kept as ``samples/<id>.wav``. Only what changed is rendered
  again, and a candidate whose key or extra is missing is skipped;
- ``page`` writes ``index.html``: one row per candidate with its player, its
  measured latency and Yes / Maybe / No / NotSet choices, Yes and Maybe tabs,
  and an export/import of the choices.

Design: specs/voice-audition.md. Usage (from the repo root):

    uv run python tools/voice-audition/audition.py new my-assistant.json \\
        --text "Hi! What would you like to talk about?" \\
        --module phonon --module cartesia --gender male --language en --limit 8
    zsh -ic 'source ~/.zshrc >/dev/null 2>&1; \\
        uv run python tools/voice-audition/audition.py render my-assistant.json'
    open my-assistant.audition/index.html
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tts_engine import (
    TTSEngine,
    TTSEngineConfig,
    VoiceInfo,
    VoiceListingUnsupported,
    list_voices,
)
from tts_engine.config import ConfigError
from tts_engine.modules import REGISTRY
from tts_engine.modules.base import TTSError

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "page.html"
_DATA_PLACEHOLDER = "/*AUDITION_DATA*/null"

_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
_TOP_KEYS = {"name", "text", "candidates"}
_CANDIDATE_KEYS = {"id", "label", "section", "notes", "config"}
_GENDERS = ("male", "female", "neutral")


class AuditionError(Exception):
    """An invalid audition file or command line, reported before any synthesis."""


# --- the audition file ------------------------------------------------------


def load_audition(path: Path) -> dict[str, Any]:
    """The audition file with its defaults filled in (`name`, `label`,
    `section`); `AuditionError` naming the file and field otherwise."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditionError(f"{path}: cannot read the audition file: {exc}") from exc
    if not isinstance(data, dict):
        raise AuditionError(f"{path}: the audition file must be a JSON object")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise AuditionError(f"{path}: unknown keys {sorted(unknown)}")

    name = data.get("name", path.stem)
    if not isinstance(name, str) or not name:
        raise AuditionError(f"{path}: 'name' must be a non-empty string")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AuditionError(f"{path}: 'text' must be a non-empty string")
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise AuditionError(f"{path}: 'candidates' must be a list")

    seen: set[str] = set()
    loaded: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        where = f"{path}: candidates[{index}]"
        if not isinstance(candidate, dict):
            raise AuditionError(f"{where} must be an object")
        unknown = set(candidate) - _CANDIDATE_KEYS
        if unknown:
            raise AuditionError(f"{where}: unknown keys {sorted(unknown)}")
        cid = candidate.get("id")
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            raise AuditionError(f"{where}: 'id' must match {_ID.pattern}, got {cid!r}")
        if cid in seen:
            raise AuditionError(f"{where}: duplicate id {cid!r}")
        seen.add(cid)
        config = candidate.get("config")
        if not isinstance(config, dict) or not isinstance(config.get("type"), str):
            raise AuditionError(
                f"{where} ({cid}): 'config' must be an object with a 'type'"
            )
        for field in ("label", "section", "notes"):
            if field in candidate and not isinstance(candidate[field], str):
                raise AuditionError(f"{where} ({cid}): {field!r} must be a string")
        loaded.append(
            {
                "id": cid,
                "label": candidate.get("label") or cid,
                "section": candidate.get("section") or config["type"],
                "notes": candidate.get("notes", ""),
                "config": config,
            }
        )
    return {"name": name, "text": text, "candidates": loaded}


def default_out_dir(audition_path: Path) -> Path:
    return audition_path.with_suffix(".audition")


# --- new ----------------------------------------------------------------------


def parse_module(value: str) -> dict[str, Any]:
    """A `--module` value: a JSON module block, or a type name, which gets the
    provider's conventional `<TYPE>_API_KEY` when it is hosted."""
    if value.lstrip().startswith("{"):
        try:
            block = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AuditionError(f"--module {value!r}: invalid JSON: {exc}") from exc
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise AuditionError(f"--module {value!r}: a JSON module needs a 'type'")
        return block
    if value not in REGISTRY:
        raise AuditionError(f"--module {value!r}: unknown module type")
    block: dict[str, Any] = {"type": value}
    if REGISTRY[value].kind == "hosted":
        block["api_key_env"] = value.upper().replace("-", "_") + "_API_KEY"
    return block


def _language_matches(code: str, wanted: str) -> bool:
    code = code.lower().replace("_", "-")
    wanted = wanted.lower().replace("_", "-")
    return code == wanted or code.startswith(wanted + "-")


def voice_matches(
    voice: VoiceInfo,
    *,
    genders: list[str],
    languages: list[str],
    query: str | None,
    keep_unknown: bool,
) -> bool:
    if genders:
        if voice.gender is None:
            if not keep_unknown:
                return False
        elif voice.gender not in genders:
            return False
    if languages:
        if not voice.languages:
            if not keep_unknown:
                return False
        elif not any(
            _language_matches(c, w) for c in voice.languages for w in languages
        ):
            return False
    if query:
        haystack = " ".join([voice.name, voice.description or "", *voice.tags]).lower()
        if query.lower() not in haystack:
            return False
    return True


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "voice"


def voice_notes(voice: VoiceInfo) -> str:
    parts = [
        voice.gender,
        voice.age,
        ", ".join(voice.languages),
        voice.description,
        ", ".join(voice.tags),
    ]
    return " · ".join(part for part in parts if part)


def build_candidates(
    base: dict[str, Any], voices: list[VoiceInfo], taken: set[str]
) -> list[dict[str, Any]]:
    """One candidate per voice; ids are `<type>-<slug>`, made unique against `taken`."""
    candidates = []
    for voice in voices:
        stem = f"{_slug(base['type'])}-{_slug(voice.name)}"
        cid, n = stem, 2
        while cid in taken:
            cid, n = f"{stem}-{n}", n + 1
        taken.add(cid)
        candidates.append(
            {
                "id": cid,
                "label": voice.name,
                "section": base["type"],
                "notes": voice_notes(voice),
                "config": {**base, **voice.config},
            }
        )
    return candidates


def cmd_new(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise AuditionError(f"{out} exists; pass --force to overwrite it")
    if not args.text.strip():
        raise AuditionError("--text must not be empty")
    bases = [parse_module(value) for value in args.module]

    candidates: list[dict[str, Any]] = []
    taken: set[str] = set()
    for base in bases:
        label = base["type"]
        try:
            voices = list_voices(base)
        except VoiceListingUnsupported as exc:
            print(f"{label}: cannot list voices: {exc}")
            continue
        except (ConfigError, TTSError) as exc:
            print(f"{label}: {type(exc).__name__}: {exc}")
            continue
        matching = [
            v
            for v in voices
            if voice_matches(
                v,
                genders=args.gender,
                languages=args.language,
                query=args.query,
                keep_unknown=args.keep_unknown,
            )
        ]
        if args.limit is not None:
            matching = matching[: args.limit]
        print(f"{label}: {len(matching)} of {len(voices)} voices")
        candidates.extend(build_candidates(base, matching, taken))

    if not candidates:
        print("no voice matched; nothing written")
        return 1
    audition = {
        "name": args.name or out.stem,
        "text": args.text,
        "candidates": candidates,
    }
    out.write_text(
        json.dumps(audition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {out}: {len(candidates)} candidates")
    return 0


# --- render -------------------------------------------------------------------


class _CaptureSink:
    """Keeps the PCM of one utterance and when its first chunk arrived."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.first_at: float | None = None

    def reset(self) -> None:
        self.chunks = []
        self.first_at = None

    def feed(self, chunk: bytes) -> None:
        if self.first_at is None and chunk:
            self.first_at = time.perf_counter()
        self.chunks.append(chunk)

    def drain(self) -> None:
        pass


def cache_key(text: str, config: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"text": text, "config": config}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _missing_key(config: dict[str, Any]) -> str | None:
    env = config.get("api_key_env")
    if (
        isinstance(env, str)
        and env
        and not config.get("api_key")
        and not os.environ.get(env)
    ):
        return f"{env} is not set"
    return None


async def render_candidate(
    candidate: dict[str, Any], text: str, *, base_dir: Path, samples: Path, runs: int
) -> dict[str, Any]:
    """Render one candidate; the results entry (without `key`)."""
    missing = _missing_key(candidate["config"])
    if missing is not None:
        return {"status": "skipped", "reason": missing}

    sink = _CaptureSink()
    try:
        engine = TTSEngine(
            TTSEngineConfig.from_dict(
                {"module": candidate["config"]}, base_dir=base_dir
            ),
            sink=sink,
        )
    except ConfigError as exc:
        if "extra" in str(exc):
            return {"status": "skipped", "reason": str(exc)}
        return {"status": "error", "error": f"ConfigError: {exc}"}
    except Exception as exc:  # noqa: BLE001 — a failing backend must not stop the pass
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    ttfa: list[float] = []
    try:
        await engine.say(text)  # warm-up: connection, model load, kernels
        for _ in range(runs):
            sink.reset()
            start = time.perf_counter()
            await engine.say(text)
            if sink.first_at is None:
                return {"status": "error", "error": "the engine produced no audio"}
            ttfa.append(round((sink.first_at - start) * 1000, 1))
    except Exception as exc:  # noqa: BLE001 — recorded, and the pass goes on
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await engine.close()

    pcm = b"".join(sink.chunks)
    rate = engine.sample_rate
    samples.mkdir(parents=True, exist_ok=True)
    with wave.open(str(samples / f"{candidate['id']}.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return {
        "status": "ok",
        "ttfa_ms": ttfa,
        "ttfa_median_ms": round(statistics.median(ttfa), 1),
        "duration_s": round(len(pcm) / 2 / rate, 2),
        "sample_rate": rate,
        "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def load_results(out: Path) -> dict[str, Any]:
    path = out / "results.json"
    if not path.exists():
        return {}
    try:
        results = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return results if isinstance(results, dict) else {}


def _write_results(out: Path, results: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )


async def render(
    audition_path: Path,
    out: Path,
    *,
    runs: int = 3,
    only: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Render the audition's candidates into `out` and write the page; the
    results by id."""
    if runs < 1:
        raise AuditionError("--runs must be at least 1")
    audition = load_audition(audition_path)
    ids = {c["id"] for c in audition["candidates"]}
    unknown = set(only or ()) - ids
    if unknown:
        raise AuditionError(
            f"--only: no candidate {sorted(unknown)} in {audition_path}"
        )

    results = {cid: r for cid, r in load_results(out).items() if cid in ids}
    samples = out / "samples"
    for candidate in audition["candidates"]:
        cid = candidate["id"]
        if only and cid not in only:
            continue
        key = cache_key(audition["text"], candidate["config"])
        previous = results.get(cid, {})
        if (
            not force
            and previous.get("status") == "ok"
            and previous.get("key") == key
            and (samples / f"{cid}.wav").exists()
        ):
            print(f"{cid}: unchanged")
            continue
        print(f"{cid}: rendering ...", flush=True)
        entry = await render_candidate(
            candidate,
            audition["text"],
            base_dir=audition_path.parent,
            samples=samples,
            runs=runs,
        )
        results[cid] = {"key": key, **entry}
        detail = entry.get("ttfa_median_ms", entry.get("reason") or entry.get("error"))
        print(f"{cid}: {entry['status']} ({detail})", flush=True)
        _write_results(out, results)

    _write_results(out, results)
    write_page(audition, results, out)
    return results


# --- page ---------------------------------------------------------------------


def _redacted(config: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k == "api_key" else v) for k, v in config.items()}


def page_data(audition: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    sections: list[str] = []
    for candidate in audition["candidates"]:
        if candidate["section"] not in sections:
            sections.append(candidate["section"])
    return {
        "name": audition["name"],
        "text": audition["text"],
        "sections": sections,
        "candidates": [
            {
                "id": c["id"],
                "label": c["label"],
                "section": c["section"],
                "notes": c["notes"],
                "config": _redacted(c["config"]),
                "result": {
                    k: v for k, v in results.get(c["id"], {}).items() if k != "key"
                },
            }
            for c in audition["candidates"]
        ],
    }


def write_page(audition: dict[str, Any], results: dict[str, Any], out: Path) -> Path:
    template = _TEMPLATE.read_text(encoding="utf-8")
    if template.count(_DATA_PLACEHOLDER) != 1:
        raise AuditionError(
            f"{_TEMPLATE}: expected one {_DATA_PLACEHOLDER} placeholder"
        )
    # Inside a <script>, "</" could end the element: escape it in the JSON.
    data = json.dumps(page_data(audition, results), ensure_ascii=False).replace(
        "</", "<\\/"
    )
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(template.replace(_DATA_PLACEHOLDER, data), encoding="utf-8")
    return page


# --- command line -------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser(
        "new", help="write an audition file from the backends' catalogs"
    )
    new.add_argument("out", help="the audition file to write (.json)")
    new.add_argument("--text", required=True, help="the sentence every candidate says")
    new.add_argument(
        "--module",
        action="append",
        required=True,
        help="a module type (hosted ones get <TYPE>_API_KEY) or a JSON module block; repeatable",
    )
    new.add_argument(
        "--gender", action="append", default=[], choices=_GENDERS, help="repeatable"
    )
    new.add_argument(
        "--language",
        action="append",
        default=[],
        help="a language code or prefix (en, en-US); repeatable",
    )
    new.add_argument("--query", help="substring of the name, description or tags")
    new.add_argument("--limit", type=int, help="at most this many voices per module")
    new.add_argument(
        "--keep-unknown",
        action="store_true",
        help="keep voices whose catalog gives no gender/language when filtering on it",
    )
    new.add_argument("--name", help="the audition's name (default: the file's stem)")
    new.add_argument("--force", action="store_true", help="overwrite an existing file")

    ren = sub.add_parser("render", help="render the candidates and write the page")
    ren.add_argument("audition", help="the audition file")
    ren.add_argument("--out", help="output folder (default: <audition>.audition)")
    ren.add_argument(
        "--runs",
        type=int,
        default=3,
        help="measured runs per candidate after one warm-up",
    )
    ren.add_argument("--only", nargs="+", help="render only these candidate ids")
    ren.add_argument(
        "--force", action="store_true", help="render again even when unchanged"
    )

    page = sub.add_parser("page", help="rewrite the page from the existing results")
    page.add_argument("audition", help="the audition file")
    page.add_argument("--out", help="output folder (default: <audition>.audition)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "new":
            return cmd_new(args)
        audition_path = Path(args.audition)
        out = Path(args.out) if args.out else default_out_dir(audition_path)
        if args.command == "page":
            page = write_page(load_audition(audition_path), load_results(out), out)
            print(f"wrote {page}")
            return 0
        results = asyncio.run(
            render(audition_path, out, runs=args.runs, only=args.only, force=args.force)
        )
    except AuditionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    counts = {
        s: sum(r.get("status") == s for r in results.values())
        for s in ("ok", "skipped", "error")
    }
    print(
        f"{counts['ok']} ok, {counts['skipped']} skipped, {counts['error']} errors; page: {out / 'index.html'}"
    )
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
