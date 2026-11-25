"""The demo page's session and declaration (specs/gradio-demo.md)."""

import concurrent.futures
import json
import threading
import time
from pathlib import Path

import gradio as gr
import pytest
from app_setup.app import AppModel
from app_setup.folder import ConfigFolder
from app_setup.secrets import SecretStore

from examples import config_app
from examples.gradio_demo.app import (
    APP,
    DEFAULT_CONFIG,
    build_page,
    open_model,
    unsaved_line,
)
from examples.gradio_demo.session import (
    SpeechSession,
    Voice,
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


def _model(tmp_path: Path) -> AppModel:
    """The demo's model over a folder of its own, its secrets kept off os.environ."""
    folder = ConfigFolder(tmp_path, APP.default_config)
    return AppModel(APP, folder, SecretStore(folder.env_path, {}))


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


def _silent(**module: object) -> dict[str, object]:
    return {"tts": {"module": {"type": "tone", **module}, "player": {"discard": True}}}


def test_the_voice_is_built_at_the_first_utterance_and_kept_while_the_config_holds(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path)
    model.save(_silent())
    voice = Voice()
    assert voice.session is None
    try:
        first = voice.say(model, "one")
        built = voice.session
        second = voice.say(model, "two")

        assert not isinstance(first, list) and first.text_chars == 3
        assert not isinstance(second, list) and second.text_chars == 3
        assert built is not None and voice.session is built
    finally:
        voice.close()
    assert voice.session is None


def test_a_saved_change_is_heard_at_the_next_utterance_and_never_locks_the_config(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path)
    model.save(_silent())
    voice = Voice()
    try:
        voice.say(model, "before")
        before = voice.session

        model.save(_silent(sample_rate=16000))  # saving is never refused
        voice.say(model, "after")

        assert voice.session is not before and voice.session is not None
        assert voice.session.sample_rate == 16000
    finally:
        voice.close()


def test_a_config_that_cannot_speak_says_why_and_builds_nothing(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path)
    model.save({"tts": {"module": {"type": "nope"}}})
    voice = Voice()

    said = voice.say(model, "hello")

    assert isinstance(said, list) and said[0].startswith("/tts/module")
    assert voice.session is None


def test_the_demo_declares_one_tts_section_whose_default_is_valid(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path)

    assert [section.name for section in APP.sections] == ["tts"]
    assert APP.bare  # its config is the engine block itself
    assert model.saved_config() == {"tts": DEFAULT_CONFIG}
    # What the machine needs for it (the phonon extra, its model) depends on the machine.
    assert model.check_config({"tts": DEFAULT_CONFIG}) == []


def test_the_page_puts_speak_first_and_opens_on_setup(tmp_path: Path) -> None:
    page = build_page(_model(tmp_path), Voice())

    tabs = [b.label for b in page.blocks.values() if isinstance(b, gr.Tab)]
    labels = [getattr(block, "label", None) for block in page.blocks.values()]
    [selector] = [b for b in page.blocks.values() if isinstance(b, gr.Tabs)]
    assert tabs == ["Speak", "Setup", "Secrets"]
    buttons = [b.value for b in page.blocks.values() if isinstance(b, gr.Button)]
    assert "Text to speak" in labels and buttons.count("Test") == 1  # the tts section's
    assert "Variable" in labels
    assert selector.selected == "setup"


def test_the_default_config_is_the_committed_file_written_into_an_empty_folder(
    tmp_path: Path,
) -> None:
    committed = json.loads(
        (
            Path(__file__).parents[2] / "examples/gradio_demo/default_config.json"
        ).read_text()
    )
    folder = tmp_path / "fresh"

    _model(folder)

    assert APP.default_config == committed
    assert committed["module"]["type"] == "phonon"  # the engine block, unwrapped
    assert json.loads((folder / "config.json").read_text()) == committed


def test_the_speak_tab_says_when_the_setup_tab_has_unsaved_changes(
    tmp_path: Path,
) -> None:
    model = _model(tmp_path)
    saved = model.saved_config()
    edited = {**saved, "tts": {"module": {"type": "tone"}}}

    assert unsaved_line(model, saved) == ""
    assert unsaved_line(model, {}) == ""  # the panel has not loaded its draft yet
    assert unsaved_line(model, edited) == (
        "The Setup tab has unsaved changes: Speak uses the saved config."
    )
    model.save(edited)
    assert unsaved_line(model, edited) == ""


# --- The three launches -------------------------------------------------------------


def _write(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


_TONE_FILE = {"module": {"type": "tone"}, "player": {"discard": True}}


def _buttons(page: gr.Blocks) -> list[object]:
    return [b.value for b in page.blocks.values() if isinstance(b, gr.Button)]


def test_the_demo_runs_from_its_folder_by_default_holding_the_engine_block(
    tmp_path: Path,
) -> None:
    model, _ = open_model(["--folder", str(tmp_path / "demo")])

    assert model.editable
    assert json.loads((tmp_path / "demo" / "config.json").read_text()) == (
        DEFAULT_CONFIG
    )
    assert model.source.env_path == tmp_path / "demo" / ".env"


def test_the_demo_speaks_with_a_config_file_as_it_is_and_never_writes_it(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "voice.json", _TONE_FILE)
    before = path.read_bytes()
    model, _ = open_model(["--config", str(path)])
    voice = Voice()
    try:
        said = voice.say(model, "hello")
        assert not isinstance(said, list) and said.text_chars == 5

        # A hand edit is heard at the next utterance, no relaunch.
        _write(path, {**_TONE_FILE, "module": {"type": "tone", "sample_rate": 16000}})
        voice.say(model, "again")
        assert voice.session is not None and voice.session.sample_rate == 16000
    finally:
        voice.close()

    assert not model.editable
    with pytest.raises(RuntimeError):
        model.save({"tts": {"module": {"type": "nope"}}})
    assert json.loads(path.read_text())["module"]["sample_rate"] == 16000
    assert before != path.read_bytes()  # only the hand edit changed it
    assert model.source.env_path == tmp_path / ".env"  # the .env next to the file


def test_a_file_s_setup_tab_is_read_only(tmp_path: Path) -> None:
    path = _write(tmp_path / "voice.json", _TONE_FILE)
    model, _ = open_model(["--config", str(path), "--env", str(tmp_path / "k.env")])

    buttons = _buttons(build_page(model, Voice()))

    # No Save, Revert or Reset: the file is spoken with as it is. The checks remain.
    assert "Revert to saved" not in buttons and "Reset to defaults" not in buttons
    assert "Speak" in buttons and "Test" in buttons and "Install all" in buttons
    assert model.source.env_path == tmp_path / "k.env"


def test_a_missing_config_file_stops_the_demo_with_its_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"

    with pytest.raises(SystemExit) as stopped:
        open_model(["--config", str(missing)])

    assert stopped.value.code == 2
    assert f"config file {missing} does not exist" in capsys.readouterr().err


def test_the_example_engine_configs_run_in_the_demo_as_they_are() -> None:
    path = Path(__file__).parents[2] / "examples" / "configs" / "tone.json"
    model, _ = open_model(["--config", str(path)])

    assert model.blockers(model.saved_config()) == []
    assert model.saved_config()["tts"]["module"] == {"type": "tone"}


def test_the_config_app_saves_the_engine_block_alone_back_to_its_file(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "voice.json", _TONE_FILE)
    model, options = config_app.open_model([str(path), "--port", "7999"])

    edited = {"tts": {"module": {"type": "tone", "sample_rate": 16000}}}
    model.save(edited)

    assert model.editable and options.port == 7999
    assert json.loads(path.read_text()) == edited["tts"]
    assert model.saved_config() == edited
    buttons = _buttons(config_app.config_page(model))
    assert "Revert to saved" in buttons and "Reset to defaults" not in buttons
    assert "Speak" not in buttons  # nothing runs


def test_the_config_app_refuses_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        config_app.open_model([str(tmp_path / "nope.json")])

    assert "does not exist" in capsys.readouterr().err
