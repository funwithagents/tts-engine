"""The demo page's session and declaration (specs/setup-demo.md)."""

import concurrent.futures
import threading
import time
from pathlib import Path

import pytest
from module_setup.app import AppModel
from module_setup.folder import ConfigFolder
from module_setup.secrets import SecretStore
from module_setup.session import SessionContext, SessionState

from examples.setup_demo.app import APP, DEFAULT_CONFIG, start
from examples.setup_demo.session import SpeechSession, describe_result
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


def test_a_config_the_engine_refuses_fails_the_start_and_frees_its_loop() -> None:
    before = threading.active_count()

    with pytest.raises(ValueError, match="Unknown TTS module type"):
        start(
            SessionContext(
                config={"tts": {"module": {"type": "nope"}}}, folder=Path(".")
            )
        )

    assert threading.active_count() == before


def test_start_builds_the_engine_from_the_saved_section(tmp_path: Path) -> None:
    session = start(
        SessionContext(
            config={
                "tts": {
                    "module": {"type": "tone", "sample_rate": 16000},
                    "player": {"discard": True},
                }
            },
            folder=tmp_path,
        )
    )
    try:
        assert session.sample_rate == 16000
        assert session.say("ok").text_chars == 2
    finally:
        session.stop()


def test_the_demo_declares_one_tts_section_whose_default_starts(tmp_path: Path) -> None:
    folder = ConfigFolder(tmp_path, APP.default_config)
    folder.ensure()
    folder.save_config({"tts": {**DEFAULT_CONFIG["tts"], "player": {"discard": True}}})
    model = AppModel(APP, folder, SecretStore(folder.env_path, {}))

    assert [section.name for section in APP.sections] == ["tts"]
    assert model.check_config(DEFAULT_CONFIG) == []
    assert model.start_blockers(DEFAULT_CONFIG) == []

    assert model.start() == []
    assert model.runner.wait(10) is SessionState.RUNNING
    assert isinstance(model.runner.session, SpeechSession)
    model.stop()
    assert model.runner.wait(10) is SessionState.STOPPED


def test_the_page_builds() -> None:
    from module_setup.ui import build_page

    page = build_page(
        AppModel(APP, ConfigFolder(Path("/nonexistent"), APP.default_config))
    )

    labels = [getattr(block, "label", None) for block in page.blocks.values()]
    assert "Text to speak" in labels
