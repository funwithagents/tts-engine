"""The config app: an engine config file edited, nothing run (specs/gradio-demo.md, "The
config app").

One engine config file (an engine block, like any of `examples/configs/`) edited with the
demo's Setup and Secrets panels: Save writes it back, Revert reads it again; nothing is
spoken, there is no session. The keys go to the `.env` next to the file unless `--env`
names another. It runs in this project's environment, so its checks and its installs are
those of the environment the config will run in.

    uv run python -m examples.config_app FILE [--env PATH] [--host ADDRESS] [--port N]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app_setup.app import AppModel
from app_setup.config_file import ConfigFileError
from app_setup.launch import add_config_app_arguments, open_config_app
from app_setup.ui import config_page

from examples.gradio_demo.app import APP

__all__ = ["main", "open_model"]


def open_model(
    argv: Sequence[str] | None = None,
) -> tuple[AppModel, argparse.Namespace]:
    """The command line, and the model over the file it names (writable); a missing or
    unreadable file stops the app with its error."""
    parser = argparse.ArgumentParser(
        prog="python -m examples.config_app",
        description="Edit a tts-engine config file.",
    )
    add_config_app_arguments(parser)
    options = parser.parse_args(argv)
    try:
        return open_config_app(options, APP), options
    except ConfigFileError as exc:
        parser.error(str(exc))


def main(argv: Sequence[str] | None = None) -> None:
    model, options = open_model(argv)
    config_page(model).launch(server_name=options.host, server_port=options.port)
