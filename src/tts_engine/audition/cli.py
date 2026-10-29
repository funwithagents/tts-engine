"""`tts-engine-audition` entry point: hear candidate voices side by side,
measure them, pick some.

An audition file (JSON) holds one sentence and a list of candidates, each a
complete ``engine.module`` block. Three commands:

- ``new`` builds an audition file from the backends' own catalogs
  (``tts_engine.list_voices``), filtered by gender, language and text;
- ``render`` synthesizes every candidate through ``TTSEngine``: one warm-up,
  then ``--runs`` timed calls whose time to first audio is recorded, the last
  run's audio kept as ``samples/<id>.wav``. Only what changed is rendered
  again, and a candidate whose key or extra is missing is skipped;
- ``page`` writes ``index.html``: one row per candidate with its player, its
  measured latency, its level with the ``gain_db`` that brings it to a target
  level, and Yes / Maybe / No / NotSet choices, Yes and Maybe tabs, and an
  export/import of the choices.

Example:

    tts-engine-audition new my-assistant.json \\
        --text "Hi! What would you like to talk about?" \\
        --module phonon --module cartesia --gender male --language en --limit 8
    tts-engine-audition render my-assistant.json
    open my-assistant.audition/index.html

Hosted backends read their key from the environment and make paid calls.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from tts_engine.audition.audition import (
    GENDERS,
    AuditionError,
    default_out_dir,
    load_audition,
    load_choices,
    load_results,
    new,
    render,
    write_page,
)
from tts_engine.level import DEFAULT_TARGET_DBFS

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tts-engine-audition",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="root log level (default: WARNING)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    new_cmd = sub.add_parser(
        "new", help="write an audition file from the backends' catalogs"
    )
    new_cmd.add_argument("out", help="the audition file to write (.json)")
    new_cmd.add_argument(
        "--text", required=True, help="the sentence every candidate says"
    )
    new_cmd.add_argument(
        "--module",
        action="append",
        required=True,
        help="a module type (hosted ones get <TYPE>_API_KEY) or a JSON module block; repeatable",
    )
    new_cmd.add_argument(
        "--gender", action="append", default=[], choices=GENDERS, help="repeatable"
    )
    new_cmd.add_argument(
        "--language",
        action="append",
        default=[],
        help="a language code or prefix (en, en-US); repeatable",
    )
    new_cmd.add_argument("--query", help="substring of the name, description or tags")
    new_cmd.add_argument(
        "--limit", type=int, help="at most this many voices per module"
    )
    new_cmd.add_argument(
        "--keep-unknown",
        action="store_true",
        help="keep voices whose catalog gives no gender/language when filtering on it",
    )
    new_cmd.add_argument(
        "--name", help="the audition's name (default: the file's stem)"
    )
    new_cmd.add_argument(
        "--force", action="store_true", help="overwrite an existing file"
    )

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
    for command in (ren, page):
        command.add_argument(
            "--target",
            type=float,
            default=DEFAULT_TARGET_DBFS,
            help="the level (dBFS) the page's suggested gain_db aims at"
            f" (default {DEFAULT_TARGET_DBFS:g}; also editable on the page)",
        )
        command.add_argument(
            "--choices",
            metavar="FILE",
            help="a choices file (the page's export) the page starts from"
            " in a browser with none stored",
        )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format=_LOG_FORMAT)
    try:
        if args.command == "new":
            return new(
                Path(args.out),
                text=args.text,
                modules=args.module,
                genders=args.gender,
                languages=args.language,
                query=args.query,
                limit=args.limit,
                keep_unknown=args.keep_unknown,
                name=args.name,
                force=args.force,
            )
        audition_path = Path(args.audition)
        out = Path(args.out) if args.out else default_out_dir(audition_path)
        choices_path = Path(args.choices) if args.choices else None
        if args.command == "page":
            audition = load_audition(audition_path)
            page = write_page(
                audition,
                load_results(out),
                out,
                target_dbfs=args.target,
                choices=load_choices(choices_path, audition) if choices_path else {},
            )
            print(f"wrote {page}")
            return 0
        results = asyncio.run(
            render(
                audition_path,
                out,
                runs=args.runs,
                only=args.only,
                force=args.force,
                target_dbfs=args.target,
                choices_path=choices_path,
            )
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
