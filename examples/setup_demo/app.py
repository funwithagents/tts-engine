"""The engine's demo page (specs/setup-demo.md).

The demo's own Session tab (Start, Stop, a text to speak) next to module-setup's Setup and
Secrets panels, over one section, `tts`, the engine block: the Setup panel chooses and
configures a voice, installs its extra and downloads its model; the Session tab builds the
engine from the saved config and speaks what is typed.

    uv run python -m examples.setup_demo [--folder PATH] [--host ADDRESS] [--port N] [--start]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import gradio as gr
from module_setup.app import App, AppModel, Section
from module_setup.folder import ConfigFolder, default_folder
from module_setup.ui import secrets_panel, setup_panel

import tts_engine.setup
from examples.setup_demo.session import Sessions, describe_result

__all__ = ["APP", "build_page", "main"]

log = logging.getLogger(__name__)

# The tone fixture: it needs nothing installed, so the page works before any choice.
DEFAULT_CONFIG = {"tts": {"module": {"type": "tone"}}}

SAMPLE_TEXT = "Hello! This is the voice you set up, speaking from the demo page."

APP = App(
    name="tts-engine-demo",
    title="tts-engine — set up a voice",
    host="tts-engine",
    sections=(Section("tts", tts_engine.setup, title="Text to speech"),),
    default_config=DEFAULT_CONFIG,
)


def _status(sessions: Sessions) -> str:
    session = sessions.session
    if session is not None:
        return f"🟢 **Running** ({session.sample_rate} Hz)"
    if sessions.busy():
        return "🟡 **Starting…**"
    return "⚪ **Stopped**"


def _session_tab(model: AppModel, sessions: Sessions) -> None:
    """Start and Stop, a text to speak, Speak, Interrupt, and the result."""
    with gr.Row():
        start = gr.Button("Start", variant="primary", scale=0)
        stop = gr.Button("Stop", variant="stop", scale=0)
    status = gr.Markdown(_status(sessions))
    refused = gr.Markdown()
    text = gr.Textbox(label="Text to speak", value=SAMPLE_TEXT, lines=3)
    with gr.Row():
        speak = gr.Button("Speak", variant="primary", scale=0)
        interrupt = gr.Button("Interrupt", scale=0)
    result = gr.Markdown()
    timer = gr.Timer(1.0)

    def on_start() -> str:
        blockers = sessions.start(model)
        if not blockers:
            return ""
        return "**The session did not start:**\n\n" + "\n".join(
            f"- {b}" for b in blockers
        )

    def on_speak(value: str) -> str:
        session = sessions.session
        if session is None:
            return "⚠️ Start a session first."
        if not value.strip():
            return "⚠️ Type something to say."
        try:
            return "✅ " + describe_result(session.say(value))
        except concurrent.futures.CancelledError:
            return "⏹ Interrupted."
        except Exception as exc:  # noqa: BLE001 - shown on the page
            return f"❌ {exc}"

    def on_interrupt() -> None:
        session = sessions.session
        if session is not None:
            session.interrupt()

    def on_tick() -> tuple[str, Any, Any]:
        busy = sessions.busy()
        return (
            _status(sessions),
            gr.update(interactive=not busy),
            gr.update(interactive=sessions.session is not None),
        )

    start.click(on_start, outputs=[refused])
    stop.click(lambda: sessions.stop() and None)
    speak.click(on_speak, inputs=[text], outputs=[result])
    text.submit(on_speak, inputs=[text], outputs=[result])
    interrupt.click(on_interrupt)
    timer.tick(on_tick, outputs=[status, start, stop], show_progress="hidden")


def build_page(model: AppModel, sessions: Sessions) -> gr.Blocks:
    """The demo's page: its Session tab, then module-setup's Setup and Secrets panels. It
    opens on the Session tab while a session runs, else on Setup."""
    with gr.Blocks(title=model.title) as page:
        gr.Markdown(f"# {model.title}")
        with gr.Tabs(selected="session" if sessions.busy() else "setup"):
            with gr.Tab("Session", id="session"):
                _session_tab(model, sessions)
            with gr.Tab("Setup", id="setup"):
                setup = setup_panel(model)
            with gr.Tab("Secrets", id="secrets"):
                secrets_panel(model, setup.draft)
    return page


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    folder = default_folder(APP.name)
    parser = argparse.ArgumentParser(description=APP.title)
    parser.add_argument(
        "--folder",
        type=Path,
        default=folder,
        help=f"the demo's folder: its config and its keys (default: {folder})",
    )
    parser.add_argument("--host", default="127.0.0.1", help="the address to serve on")
    parser.add_argument("--port", type=int, default=None, help="the port to serve on")
    parser.add_argument(
        "--start",
        action="store_true",
        help="start a session with the saved config at launch",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    options = _arguments(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    sessions = Sessions()
    # Building the model makes the folder ready and loads its .env.
    model = AppModel(
        APP, ConfigFolder(options.folder, APP.default_config), locked=sessions.busy
    )
    log.info("folder: %s", model.path)
    if options.start and (blockers := sessions.start(model)):
        log.warning("no session at launch: %s", "; ".join(blockers))

    try:
        build_page(model, sessions).launch(
            server_name=options.host, server_port=options.port
        )
    finally:
        sessions.stop()


if __name__ == "__main__":
    main()
