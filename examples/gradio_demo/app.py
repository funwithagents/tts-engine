"""The engine's demo page (specs/gradio-demo.md).

The demo's own Speak tab next to module-setup's Setup and Secrets panels, over one section,
`tts`, the engine block: the Setup panel chooses and configures a voice, installs its extra
and downloads its model; Speak builds the engine from the saved config when it needs it
(again after a save) and says what is typed. Nothing runs between utterances, so there is
no Start, no Stop and no lock on the config.

    uv run python -m examples.gradio_demo [--folder PATH] [--host ADDRESS] [--port N]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import gradio as gr
from module_setup.app import App, AppModel, Section
from module_setup.folder import ConfigFolder, default_folder
from module_setup.ui import secrets_panel, setup_panel

import tts_engine.setup
from examples.gradio_demo.session import Voice, describe_result

__all__ = ["APP", "build_page", "main"]

log = logging.getLogger(__name__)

# Written into an empty folder on first launch: the Phonon voice, a local model. On a
# fresh install the Setup tab says what it needs (its extra, its model download).
DEFAULT_CONFIG = json.loads(
    (Path(__file__).parent / "default_config.json").read_text(encoding="utf-8")
)

SAMPLE_TEXT = "Hello! This is the voice you set up, speaking from the demo page."

APP = App(
    name="tts-engine-demo",
    title="tts-engine — set up a voice",
    host="tts-engine",
    sections=(Section("tts", tts_engine.setup, title="Text to speech"),),
    default_config=DEFAULT_CONFIG,
)


def _speak_tab(model: AppModel, voice: Voice) -> None:
    """A text to speak, Speak, Interrupt, and the result."""
    text = gr.Textbox(label="Text to speak", value=SAMPLE_TEXT, lines=3)
    with gr.Row():
        speak = gr.Button("Speak", variant="primary", scale=0)
        interrupt = gr.Button("Interrupt", scale=0)
    result = gr.Markdown()

    def on_speak(value: str) -> str:
        if not value.strip():
            return "⚠️ Type something to say."
        try:
            said = voice.say(model, value)
        except concurrent.futures.CancelledError:
            return "⏹ Interrupted."
        except Exception as exc:  # noqa: BLE001 - shown on the page
            return f"❌ {exc}"
        if isinstance(said, list):
            return "**The saved config cannot speak yet:**\n\n" + "\n".join(
                f"- {reason}" for reason in said
            )
        return "✅ " + describe_result(said)

    speak.click(on_speak, inputs=[text], outputs=[result])
    text.submit(on_speak, inputs=[text], outputs=[result])
    interrupt.click(lambda: voice.interrupt() and None)


def build_page(model: AppModel, voice: Voice) -> gr.Blocks:
    """The demo's page: its Speak tab, then module-setup's Setup and Secrets panels. It
    opens on Setup."""
    with gr.Blocks(title=model.title) as page:
        gr.Markdown(f"# {model.title}")
        with gr.Tabs(selected="setup"):
            with gr.Tab("Speak", id="speak"):
                _speak_tab(model, voice)
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    options = _arguments(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    voice = Voice()
    # Building the model makes the folder ready and loads its .env. Nothing locks the
    # config: the voice is rebuilt from it at the next utterance after a save.
    model = AppModel(APP, ConfigFolder(options.folder, APP.default_config))
    log.info("folder: %s", model.path)

    try:
        build_page(model, voice).launch(
            server_name=options.host, server_port=options.port
        )
    finally:
        voice.close()


if __name__ == "__main__":
    main()
