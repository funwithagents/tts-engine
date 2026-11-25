"""The engine's demo page (specs/gradio-demo.md).

The demo's own Speak tab next to app-setup's Setup and Secrets panels, over one section,
`tts`, the engine block: the Setup panel chooses and configures a voice, installs its extra
and downloads its model; Speak builds the engine from the saved config when it needs it
(again after a save) and says what is typed. Nothing runs between utterances, so there is
no Start, no Stop and no lock on the config.

The app is bare: its config is the engine block itself, in its folder's `config.json` or in
a file given with `--config` (any of `examples/configs/`), which is spoken with as it is, the
Setup tab read-only.

    uv run python -m examples.gradio_demo [--folder DIR | --config FILE] [--env PATH]
        [--host ADDRESS] [--port N]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import gradio as gr
from app_setup.app import App, AppModel, Section
from app_setup.config_file import ConfigFileError
from app_setup.launch import add_config_arguments, open_source
from app_setup.ui import secrets_panel, setup_panel

import tts_engine.setup
from examples.gradio_demo.session import Voice, describe_result

__all__ = ["APP", "build_page", "main"]

log = logging.getLogger(__name__)

# Written into an empty folder on first launch: the Phonon voice, a local model. On a
# fresh install the Setup tab says what it needs (its extra, its model download). The
# engine block alone: the app is bare.
DEFAULT_CONFIG = json.loads(
    (Path(__file__).parent / "default_config.json").read_text(encoding="utf-8")
)

SAMPLE_TEXT = "Hello! This is the voice you set up, speaking from the demo page."

APP = App(
    name="tts-engine-demo",
    title="tts-engine — set up a voice",
    host="tts-engine",
    sections=(Section("tts", tts_engine.setup, title="Text to speech", open="always"),),
    default_config=DEFAULT_CONFIG,
    bare=True,
)


UNSAVED = "The Setup tab has unsaved changes: Speak uses the saved config."


def unsaved_line(model: AppModel, draft: dict) -> str:
    """The Speak tab's line about the Setup tab's draft: what app-setup's `unsaved_notice`
    says next to a Start button, worded for Speak; "" when there is nothing unsaved (or
    the panel has not loaded its draft yet)."""
    return UNSAVED if draft and model.unsaved(draft) else ""


def _speak_tab(model: AppModel, voice: Voice) -> gr.Markdown:
    """A text to speak, Speak, Interrupt, and the result; the line about unsaved changes,
    returned for the page to update."""
    notice = gr.Markdown()
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
    return notice


def build_page(model: AppModel, voice: Voice) -> gr.Blocks:
    """The demo's page: its Speak tab, then app-setup's Setup and Secrets panels. It
    opens on Setup."""
    with gr.Blocks(title=model.title) as page:
        gr.Markdown(f"# {model.title}")
        with gr.Tabs(selected="setup"):
            with gr.Tab("Speak", id="speak"):
                notice = _speak_tab(model, voice)
            with gr.Tab("Setup", id="setup"):
                setup = setup_panel(model)
            with gr.Tab("Secrets", id="secrets"):
                secrets_panel(model, setup.draft)

        def follow(draft: dict) -> str:
            return unsaved_line(model, draft)

        setup.draft.change(follow, inputs=[setup.draft], outputs=[notice])
        setup.saved.change(follow, inputs=[setup.draft], outputs=[notice])
    return page


def open_model(
    argv: Sequence[str] | None = None,
) -> tuple[AppModel, argparse.Namespace]:
    """The demo's command line, and the model over the source it names: the folder
    (``--folder``, by default ``~/.config/tts-engine-demo``), or a file spoken with as it
    is (``--config``: never written, the Setup tab read-only); the keys from the ``.env``
    next to the config unless ``--env`` names another. A missing or unreadable file stops
    the demo with its error, before any page."""
    parser = argparse.ArgumentParser(
        prog="python -m examples.gradio_demo", description=APP.title
    )
    add_config_arguments(parser, APP)
    parser.add_argument("--host", default="127.0.0.1", help="the address to serve on")
    parser.add_argument("--port", type=int, default=None, help="the port to serve on")
    options = parser.parse_args(argv)
    try:
        source = open_source(options, APP)
    except ConfigFileError as exc:
        parser.error(str(exc))
    # Building the model makes a folder ready and loads the .env. Nothing locks the
    # config: the voice is rebuilt from it at the next utterance after a save (or, for a
    # file, a hand edit).
    return AppModel(APP, source), options


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    model, options = open_model(argv)
    log.info("config: %s", model.source.config_path)
    voice = Voice()

    try:
        build_page(model, voice).launch(
            server_name=options.host, server_port=options.port
        )
    finally:
        voice.close()


if __name__ == "__main__":
    main()
