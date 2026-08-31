"""Logging setup for entry points.

Configures the package logger (`tts_engine`) only — never the root logger and
never via `basicConfig`. Every module's `logging.getLogger(__name__)` is a child
of this logger and inherits its handler and level.
"""
import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int | str = "INFO") -> None:
    logger = logging.getLogger("tts_engine")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
