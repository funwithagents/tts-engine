"""The engine's demo page (specs/setup-demo.md).

A module-setup app with one section, `tts`, the engine block: the Setup tab chooses and
configures a voice, installs its extra and downloads its model; the Session tab builds the
engine from the saved config and speaks what is typed.

    uv run python -m examples.setup_demo [--folder PATH] [--port N]
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Sequence

import gradio as gr
from module_setup.app import App, Section
from module_setup.session import SessionContext, SessionRunner
from module_setup.ui import launch

import tts_engine.setup
from examples.setup_demo.session import SpeechSession, describe_result
from tts_engine.config import TTSEngineConfig

__all__ = ["APP", "main", "start"]

# The tone fixture: it needs nothing installed, so the page works before any choice.
DEFAULT_CONFIG = {"tts": {"module": {"type": "tone"}}}

SAMPLE_TEXT = "Hello! This is the voice you set up, speaking from the demo page."


def start(context: SessionContext) -> SpeechSession:
    """The session: the engine built from the saved `tts` section. A relative path in the
    module block (a voice file) resolves against the app's folder."""
    config = TTSEngineConfig.from_dict(
        dict(context.config["tts"]), base_dir=context.folder
    )
    return SpeechSession(config)


def session_tab(runner: SessionRunner) -> None:
    """The Session tab's content: a text to speak, Speak, Interrupt, and the result."""
    text = gr.Textbox(label="Text to speak", value=SAMPLE_TEXT, lines=3)
    with gr.Row():
        speak = gr.Button("Speak", variant="primary", scale=0)
        interrupt = gr.Button("Interrupt", scale=0)
    result = gr.Markdown()

    def on_speak(value: str) -> str:
        session = runner.session
        if not isinstance(session, SpeechSession):
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
        session = runner.session
        if isinstance(session, SpeechSession):
            session.interrupt()

    speak.click(on_speak, inputs=[text], outputs=[result])
    text.submit(on_speak, inputs=[text], outputs=[result])
    interrupt.click(on_interrupt)


APP = App(
    name="tts-engine-demo",
    title="tts-engine — set up a voice",
    host="tts-engine",
    sections=(Section("tts", tts_engine.setup, title="Text to speech"),),
    default_config=DEFAULT_CONFIG,
    start=start,
    session_ui=session_tab,
)


def main(argv: Sequence[str] | None = None) -> None:
    launch(APP, argv)


if __name__ == "__main__":
    main()
