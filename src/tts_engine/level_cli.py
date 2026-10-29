"""`tts-engine-level` entry point: config → engine → measured level →
the suggested `gain_db` for a target level (see tts_engine.level)."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from tts_engine.config import ConfigError, TTSEngineConfig
from tts_engine.level import DEFAULT_TARGET_DBFS, DEFAULT_TEXTS, calibrate
from tts_engine.modules.base import TTSError

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _load(path: str) -> TTSEngineConfig:
    """An engine block, or a whole MCP-server config whose `engine` it takes;
    relative paths resolve against the file's directory."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    block = data["engine"] if isinstance(data, dict) and "engine" in data else data
    return TTSEngineConfig.from_dict(block, base_dir=Path(path).resolve().parent)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tts-engine-level",
        description="Measure an engine configuration's speech level and suggest"
        " the gain_db that brings it to a target level. The texts are"
        " synthesized (a hosted module makes paid calls); nothing is played.",
    )
    parser.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="an engine block, or an MCP-server config with an engine block",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=DEFAULT_TARGET_DBFS,
        metavar="DBFS",
        help=f"the target level, -60 to 0 (default {DEFAULT_TARGET_DBFS:g})",
    )
    parser.add_argument(
        "--text",
        action="append",
        metavar="TEXT",
        help=f"a text to say instead of the {len(DEFAULT_TEXTS)} built-in ones; repeatable",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="root log level (default: WARNING)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format=_LOG_FORMAT)
    texts = args.text or list(DEFAULT_TEXTS)
    try:
        config = _load(args.config)
        print(
            f"Synthesizing {len(texts)} text(s), {sum(map(len, texts))} characters,"
            f" with the {config.module['type']} module ...",
            file=sys.stderr,
            flush=True,
        )
        result = asyncio.run(calibrate(config, target_dbfs=args.target, texts=texts))
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except TTSError as exc:
        print(f"synthesis failed: {exc}", file=sys.stderr)
        return 1

    level, suggestion = result.level, result.suggestion
    if args.json:
        print(
            json.dumps(
                {
                    "module": config.module["type"],
                    "duration_s": result.duration_s,
                    **asdict(level),
                    **asdict(suggestion),
                    "clips": suggestion.clips,
                    "current_gain_db": config.gain_db,
                },
                indent=2,
            )
        )
    else:
        rows = [
            ("measured", f"{result.duration_s:.1f} s of audio"),
            ("level", f"{level.level_dbfs:+.1f} dBFS (active speech)"),
            ("peak", f"{level.peak_dbfs:+.1f} dBFS"),
            ("target", f"{suggestion.target_dbfs:+.1f} dBFS"),
            ("gain_db", f"{suggestion.gain_db:+.1f}"),
            ("peak after", f"{suggestion.peak_after_dbfs:+.1f} dBFS"),
        ]
        if config.gain_db:
            rows.append(("current", f"{config.gain_db:+.1f} (ignored while measuring)"))
        for name, value in rows:
            print(f"{name:<11} {value}")
    if suggestion.limited:
        print(
            f"warning: gain_db stops at {suggestion.gain_db:+g}, the field's limit,"
            " short of the target",
            file=sys.stderr,
        )
    if suggestion.clips:
        print(
            "warning: this gain pushes the peak past full scale and would clip;"
            " choose a lower target",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
