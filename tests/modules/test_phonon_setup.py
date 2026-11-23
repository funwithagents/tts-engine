"""What a phonon config needs on the machine (`setup.check` / `setup.prepare`),
with the ptts runtime faked: no model is loaded, nothing is downloaded."""

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError


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


def _status(items: list[dict[str, Any]]) -> list[tuple[str, str, bool]]:
    return [(item["id"], item["status"], item["preparable"]) for item in items]


def test_a_hub_model_and_a_catalog_voice_are_unknown_and_checking_loads_nothing(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "voice": "Juniper"}}

    items = setup.check(config)

    assert _status(items) == [
        ("phonon:model", "unknown", True),
        ("phonon:voice", "unknown", True),
    ]
    assert "hf://gradium/phonon-31m-en" in items[0]["detail"]
    assert "'Juniper'" in items[1]["detail"]
    assert ptts.loads == []


def test_without_a_voice_the_model_is_the_only_item(ptts: _FakePtts) -> None:
    config = {"module": {"type": "phonon", "model": "hf://gradium/phonon-40m-fr"}}

    items = setup.check(config)

    assert _status(items) == [("phonon:model", "unknown", True)]
    assert "hf://gradium/phonon-40m-fr" in items[0]["detail"]


def test_prepare_loads_the_model_as_the_constructor_does(ptts: _FakePtts) -> None:
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

    setup.prepare({"module": module}, progress.append)

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
    assert [(p["id"], p["fraction"]) for p in progress] == [
        ("phonon:model", None),
        ("phonon:voice", None),
        ("phonon:model", 1.0),
        ("phonon:voice", 1.0),
    ]


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

    before = setup.check(config)
    assert _status(before) == [
        ("phonon:model", "missing", False),
        ("phonon:voice", "missing", False),
    ]
    assert str(tmp_path / "checkpoint") in before[0]["detail"]
    assert str(tmp_path / "voices" / "me.safetensors") in before[1]["detail"]

    setup.prepare(config)  # nothing it can fetch
    assert ptts.loads == []

    (tmp_path / "checkpoint").mkdir()
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "me.safetensors").write_bytes(b"")
    assert _status(setup.check(config)) == [
        ("phonon:model", "ready", False),
        ("phonon:voice", "ready", False),
    ]


def test_a_hub_model_with_a_local_voice_prepares_the_model_only(
    ptts: _FakePtts, tmp_path: Path
) -> None:
    voice = tmp_path / "me.safetensors"
    voice.write_bytes(b"")
    config = {"module": {"type": "phonon", "voice": str(voice)}}
    progress: list[dict[str, Any]] = []

    assert _status(setup.check(config)) == [
        ("phonon:model", "unknown", True),
        ("phonon:voice", "ready", False),
    ]
    setup.prepare(config, progress.append)

    # The file is registered after the load, under its stem, not passed as a name.
    assert ptts.loads == [{"lang": "en"}]
    assert ptts.added_voices == [("me", str(voice))]
    assert {p["id"] for p in progress} == {"phonon:model"}


def test_without_the_extra_the_items_are_unknown_and_prepare_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ptts", None)  # as in a base install
    config = {"module": {"type": "phonon", "voice": "Juniper"}}

    items = setup.check(config)

    assert _status(items) == [
        ("phonon:model", "unknown", True),
        ("phonon:voice", "unknown", True),
    ]
    assert all("phonon extra is not installed" in item["detail"] for item in items)
    with pytest.raises(ConfigError, match=r"pip install tts-engine\[phonon\]"):
        setup.prepare(config)


def test_a_failed_download_is_a_tts_error_and_a_rejected_value_a_config_error(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "voice": "Nobody"}}

    ptts.raise_on_load = RuntimeError("hub unreachable")
    with pytest.raises(TTSError, match="phonon model load failed: hub unreachable"):
        setup.prepare(config)

    ptts.raise_on_load = LookupError("unknown voice 'Nobody'")
    with pytest.raises(ConfigError, match="Phonon module: unknown voice 'Nobody'"):
        setup.prepare(config)


def test_an_invalid_config_is_refused_before_anything_is_looked_up(
    ptts: _FakePtts,
) -> None:
    config = {"module": {"type": "phonon", "language": "it"}}

    with pytest.raises(ConfigError, match="'language' must be one of"):
        setup.check(config)
    with pytest.raises(ConfigError, match="'language' must be one of"):
        setup.prepare(config)
    assert ptts.loads == []
