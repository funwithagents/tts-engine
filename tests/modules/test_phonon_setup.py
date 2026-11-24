"""What a phonon config needs on the machine (`setup.check` / `setup.prepare`),
with the ptts runtime faked: no model is loaded, nothing is downloaded."""

import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError


def _files(config: dict) -> list[dict]:
    """The module's file items (`check_files`), with its library faked."""
    from tts_engine.modules import REGISTRY

    return REGISTRY[config["module"]["type"]].check_files(config["module"])


class _FakePtts:
    """Stands in for `ptts`: records each model load."""

    def __init__(self) -> None:
        self.loads: list[dict[str, Any]] = []
        self.added_voices: list[tuple[str, str]] = []
        self.threads_set: list[int] = []
        self.raise_on_load: Exception | None = None

    def module(self) -> types.SimpleNamespace:
        fake = self

        class FakeTTS:
            sample_rate = 24000

            def __init__(self, **kwargs: Any) -> None:
                fake.loads.append(kwargs)
                if fake.raise_on_load is not None:
                    raise fake.raise_on_load

            def add_voice(self, name: str, path: str) -> None:
                fake.added_voices.append((name, path))

        return types.SimpleNamespace(
            TTS=FakeTTS, set_num_threads=self.threads_set.append
        )


@pytest.fixture
def ptts(monkeypatch: pytest.MonkeyPatch) -> _FakePtts:
    from tts_engine.modules.phonon import module as phonon_module

    fake = _FakePtts()
    monkeypatch.setitem(sys.modules, "ptts", fake.module())
    monkeypatch.setattr(phonon_module, "_pool_sized", False)  # a fresh process
    monkeypatch.setattr(phonon_module, "_pool_threads", None)
    return fake


def _status(items: list[dict[str, Any]]) -> list[tuple[str, str, bool, bool]]:
    return [
        (item["id"], item["status"], item["preparable"], item["required"])
        for item in items
    ]


def test_a_hub_model_and_a_catalog_voice_are_unknown_and_checking_loads_nothing(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "voice": "Juniper"}}

    items = _files(config)

    assert _status(items) == [
        ("phonon:model", "unknown", True, False),
        ("phonon:voice", "unknown", True, False),
    ]
    assert "hf://gradium/phonon-31m-en" in items[0]["detail"]
    assert "'Juniper'" in items[1]["detail"]
    assert ptts.loads == []


def test_without_a_voice_the_model_is_the_only_item(ptts: _FakePtts) -> None:
    config = {"module": {"type": "phonon", "model": "hf://gradium/phonon-40m-fr"}}

    items = _files(config)

    assert _status(items) == [("phonon:model", "unknown", True, False)]
    assert "hf://gradium/phonon-40m-fr" in items[0]["detail"]


def test_preparing_the_voice_loads_the_model_with_it_as_the_constructor_does(
    ptts: _FakePtts,
) -> None:
    module = {
        "type": "phonon",
        "voice": "Juniper",
        "language": "none",
        "model": "hf://gradium/phonon-40m-multilingual",
        "device": "cpu",
        "quant": "q8_0",
        "threads": 2,
    }
    progress: list[dict[str, Any]] = []

    setup.prepare({"module": module}, "phonon:voice", on_progress=progress.append)

    assert ptts.loads == [
        {
            "lang": None,
            "model": "hf://gradium/phonon-40m-multilingual",
            "device": "cpu",
            "quant": "q8_0",
            "voice": "Juniper",
        }
    ]
    assert ptts.threads_set == [2]
    # ptts reports nothing while it loads: unmeasured, then ready.
    assert [p["fraction"] for p in progress] == [None, 1.0]
    assert "id" not in progress[0]


def test_preparing_the_model_loads_it_without_the_voice(ptts: _FakePtts) -> None:
    config = {"module": {"type": "phonon", "voice": "Juniper"}}

    setup.prepare(config, "phonon:model")

    assert ptts.loads == [{"lang": "en"}]


def test_the_model_and_the_voice_prepared_together_load_one_at_a_time(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "voice": "Juniper"}}
    loading = threading.Event()
    overlapped: list[bool] = []
    library: Any = sys.modules["ptts"]
    original = library.TTS

    def slow(**kwargs: Any) -> Any:
        overlapped.append(loading.is_set())
        loading.set()
        time.sleep(0.05)
        loading.clear()
        return original(**kwargs)

    library.TTS = slow
    errors: list[BaseException] = []

    def prepare(item: str) -> None:
        try:
            setup.prepare(config, item)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=prepare, args=(item,))
        for item in ("phonon:model", "phonon:voice")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert overlapped == [False, False]
    assert sorted(load.get("voice", "") for load in ptts.loads) == ["", "Juniper"]


def test_local_paths_are_ready_or_missing_and_never_prepared(
    ptts: _FakePtts, tmp_path: Path
) -> None:
    config = {
        "module": {
            "type": "phonon",
            "model": "checkpoint",
            "voice": "voices/me.safetensors",
            "base_dir": str(tmp_path),
        }
    }

    before = _files(config)
    assert _status(before) == [
        ("phonon:model", "missing", False, True),
        ("phonon:voice", "missing", False, True),
    ]
    assert str(tmp_path / "checkpoint") in before[0]["detail"]
    assert str(tmp_path / "voices" / "me.safetensors") in before[1]["detail"]

    for item in ("phonon:model", "phonon:voice"):  # nothing it can fetch
        with pytest.raises(ValueError, match=f"'{item}' is not an item"):
            setup.prepare(config, item)
    assert ptts.loads == []

    (tmp_path / "checkpoint").mkdir()
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "me.safetensors").write_bytes(b"")
    assert _status(_files(config)) == [
        ("phonon:model", "ready", False, True),
        ("phonon:voice", "ready", False, True),
    ]


def test_a_hub_model_with_a_local_voice_prepares_the_model_only(
    ptts: _FakePtts, tmp_path: Path
) -> None:
    voice = tmp_path / "me.safetensors"
    voice.write_bytes(b"")
    config = {"module": {"type": "phonon", "voice": str(voice)}}
    progress: list[dict[str, Any]] = []

    assert _status(_files(config)) == [
        ("phonon:model", "unknown", True, False),
        ("phonon:voice", "ready", False, True),
    ]
    setup.prepare(config, "phonon:model", on_progress=progress.append)

    assert ptts.loads == [{"lang": "en"}]
    assert progress[-1]["fraction"] == 1.0
    with pytest.raises(ValueError, match="'phonon:voice' is not an item"):
        setup.prepare(config, "phonon:voice")


def test_without_the_extra_only_the_extra_is_reported_and_prepare_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ptts", None)  # as in a base install
    config = {"module": {"type": "phonon", "voice": "Juniper"}}

    items = setup.check_setup(config)

    assert _status(items) == [("extra:tts-engine[phonon]", "missing", False, True)]
    assert "pip install tts-engine[phonon]" in items[0]["detail"]
    with pytest.raises(ConfigError, match=r"pip install tts-engine\[phonon\]"):
        setup.prepare(config, "phonon:voice")


def test_a_failed_download_is_a_tts_error_and_a_rejected_value_a_config_error(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "voice": "Nobody"}}

    ptts.raise_on_load = RuntimeError("hub unreachable")
    with pytest.raises(TTSError, match="phonon model load failed: hub unreachable"):
        setup.prepare(config, "phonon:voice")

    ptts.raise_on_load = LookupError("unknown voice 'Nobody'")
    with pytest.raises(ConfigError, match="Phonon module: unknown voice 'Nobody'"):
        setup.prepare(config, "phonon:voice")


def test_an_invalid_config_is_refused_before_anything_is_looked_up(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "language": "it"}}

    with pytest.raises(ConfigError, match="'language' must be one of"):
        _files(config)
    with pytest.raises(ConfigError, match="'language' must be one of"):
        setup.prepare(config, "phonon:voice")
    assert ptts.loads == []
