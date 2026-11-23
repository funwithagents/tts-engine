"""The demo page's session and declaration (specs/setup-demo.md)."""

import concurrent.futures
import threading
import time
from pathlib import Path

import gradio as gr
import pytest
from module_setup.app import AppModel
from module_setup.folder import ConfigFolder
from module_setup.secrets import SecretStore

from examples.setup_demo.app import APP, DEFAULT_CONFIG, build_page
from examples.setup_demo.session import (
    Sessions,
    SpeechSession,
    build_session,
    describe_result,
)
from tts_engine.config import TTSEngineConfig


class _Recording:
    """A sink that keeps what it is fed; `gate` holds each chunk until it is set."""

    def __init__(self) -> None:
        self.audio = b""
        self.gate = threading.Event()
        self.gate.set()

    def feed(self, chunk: bytes) -> None:
        self.gate.wait(5)
        self.audio += chunk

    def drain(self) -> None:
        pass


def _tone(**module: object) -> TTSEngineConfig:
    return TTSEngineConfig.from_dict({"module": {"type": "tone", **module}})


def test_a_session_speaks_and_reports_what_it_said() -> None:
    sink = _Recording()
    session = SpeechSession(_tone(seconds_per_char=0.01), sink=sink)
    try:
        result = session.say("Hello there")
    finally:
        session.stop()

    assert result.text_chars == len("Hello there")
    assert len(sink.audio) == round(len("Hello there") * 0.01 * 24000) * 2
    line = describe_result(result)
    assert line.startswith("11 characters, 0.11 s of audio; first audio after ")


def test_an_utterance_in_progress_can_be_interrupted() -> None:
    sink = _Recording()
    sink.gate.clear()
    session = SpeechSession(_tone(), sink=sink)
    outcome: list[object] = []

    def speak() -> None:
        try:
            outcome.append(session.say("a long sentence to interrupt"))
        except concurrent.futures.CancelledError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=speak)
    worker.start()
    for _ in range(100):
        if session.interrupt():
            break
        time.sleep(0.01)
    sink.gate.set()
    worker.join(5)
    session.stop()

    assert isinstance(outcome[0], concurrent.futures.CancelledError)
    assert not session.interrupt()


def _model(tmp_path: Path, sessions: Sessions) -> AppModel:
    """The demo's model over a folder of its own, its secrets kept off os.environ."""
    folder = ConfigFolder(tmp_path, APP.default_config)
    return AppModel(APP, folder, SecretStore(folder.env_path, {}), locked=sessions.busy)


SILENT = {"tts": {**DEFAULT_CONFIG["tts"], "player": {"discard": True}}}


def test_a_config_the_engine_refuses_fails_the_start_and_frees_its_loop() -> None:
    before = threading.active_count()

    with pytest.raises(ValueError, match="Unknown TTS module type"):
        build_session({"module": {"type": "nope"}}, Path("."))

    assert threading.active_count() == before


def test_a_session_is_the_engine_built_from_the_saved_section(tmp_path: Path) -> None:
    session = build_session(
        {"module": {"type": "tone", "sample_rate": 16000}, "player": {"discard": True}},
        tmp_path,
    )
    try:
        assert session.sample_rate == 16000
        assert session.say("ok").text_chars == 2
    finally:
        session.stop()


def test_a_session_starts_from_the_saved_config_and_locks_it_until_stopped(
    tmp_path: Path,
) -> None:
    sessions = Sessions()
    model = _model(tmp_path, sessions)
    model.save(SILENT)

    assert sessions.start(model) == []
    assert sessions.session is not None and sessions.busy()
    assert sessions.start(model) == ["A session is already running."]
    with pytest.raises(RuntimeError, match="Stop the session"):
        model.save(DEFAULT_CONFIG)

    assert sessions.stop() is True
    assert not sessions.busy() and sessions.stop() is False
    model.save(DEFAULT_CONFIG)


def test_a_session_does_not_start_with_a_config_that_has_problems(
    tmp_path: Path,
) -> None:
    sessions = Sessions()
    model = _model(tmp_path, sessions)
    model.save({"tts": {"module": {"type": "nope"}}})

    blockers = sessions.start(model)

    assert blockers and blockers[0].startswith("/tts/module")
    assert sessions.session is None and not sessions.busy()


def test_the_demo_declares_one_tts_section_whose_default_is_ready(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path, Sessions())

    assert [section.name for section in APP.sections] == ["tts"]
    assert model.saved_config() == DEFAULT_CONFIG
    assert model.blockers(DEFAULT_CONFIG) == []


def test_the_page_puts_the_session_first_and_opens_on_setup(tmp_path: Path) -> None:
    sessions = Sessions()

    page = build_page(_model(tmp_path, sessions), sessions)

    tabs = [b.label for b in page.blocks.values() if isinstance(b, gr.Tab)]
    labels = [getattr(block, "label", None) for block in page.blocks.values()]
    [selector] = [b for b in page.blocks.values() if isinstance(b, gr.Tabs)]
    assert tabs == ["Session", "Setup", "Secrets"]
    assert "Text to speak" in labels and "Section to test" in labels
    assert "Variable" in labels
    assert selector.selected == "setup"
