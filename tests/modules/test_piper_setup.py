"""What a piper config needs on the machine (specs/setup.md, "`check` and
`prepare`"), with the Hugging Face cache and download faked: no network."""

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError
from tts_engine.modules.piper import PiperModule


def _files(config: dict) -> list[dict]:
    """The module's file items (`check_files`), with its library faked."""
    from tts_engine.modules import REGISTRY

    return REGISTRY[config["module"]["type"]].check_files(config["module"])


_REPO = "rhasspy/piper-voices"
_ONNX = "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx"


class _Hub:
    """A fake `huggingface_hub`: a cache that `hf_hub_download` fills."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cache: set[tuple[str, str, str | None]] = set()
        self.downloads: list[tuple[str, str, str | None]] = []
        self.fail: Exception | None = None
        module: Any = types.ModuleType("huggingface_hub")
        module.try_to_load_from_cache = self.cached
        module.hf_hub_download = self.download
        monkeypatch.setitem(sys.modules, "huggingface_hub", module)

    def cached(
        self, repo_id: str, filename: str, revision: str | None = None
    ) -> str | None:
        key = (repo_id, filename, revision)
        return f"/cache/{filename}" if key in self.cache else None

    def download(self, repo_id: str, filename: str, revision: str | None = None) -> str:
        if self.fail is not None:
            raise self.fail
        self.downloads.append((repo_id, filename, revision))
        self.cache.add((repo_id, filename, revision))
        return f"/cache/{filename}"


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> _Hub:
    return _Hub(monkeypatch)


def test_a_catalog_voice_s_two_files_are_missing_until_prepared(hub: _Hub) -> None:
    config = {"module": {"type": "piper"}}

    before = _files(config)
    assert [(item["id"], item["status"]) for item in before] == [
        ("piper:model", "missing"),
        ("piper:config", "missing"),
    ]
    assert all(item["preparable"] for item in before)
    assert _ONNX in before[0]["detail"]

    progress: list[dict[str, Any]] = []
    setup.prepare(config, progress.append)

    assert [d[:2] for d in hub.downloads] == [(_REPO, _ONNX), (_REPO, f"{_ONNX}.json")]
    # The catalog is pinned: both files come from the same commit.
    assert len({revision for _, _, revision in hub.downloads}) == 1
    assert all(revision for _, _, revision in hub.downloads)
    assert [p["id"] for p in progress if p["fraction"] == 1.0] == [
        "piper:model",
        "piper:config",
    ]
    after = _files(config)
    assert [item["status"] for item in after] == ["ready", "ready"]
    assert after[1]["detail"] == f"/cache/{_ONNX}.json"

    setup.prepare(config)  # everything is ready: nothing more is fetched
    assert len(hub.downloads) == 2


def test_only_the_missing_file_is_downloaded(hub: _Hub) -> None:
    config = {"module": {"type": "piper", "voice": "fr_FR-siwis-medium"}}
    onnx = "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx"
    PiperModule.prepare(config["module"])
    hub.cache = {key for key in hub.cache if key[1] == onnx}
    hub.downloads.clear()

    assert [item["status"] for item in _files(config)] == ["ready", "missing"]
    setup.prepare(config)

    assert [d[1] for d in hub.downloads] == [f"{onnx}.json"]


def test_an_hf_model_reference_is_looked_up_at_its_own_revision(hub: _Hub) -> None:
    module = {"type": "piper", "model": "hf://me/voices/custom/v.onnx@abc123"}

    PiperModule.prepare(module)

    assert hub.downloads == [
        ("me/voices", "custom/v.onnx", "abc123"),
        ("me/voices", "custom/v.onnx.json", "abc123"),
    ]
    assert [item["status"] for item in PiperModule.check_files(module)] == [
        "ready",
        "ready",
    ]


def test_a_local_model_is_ready_or_missing_and_never_preparable(
    hub: _Hub, tmp_path: Path
) -> None:
    # Relative to the config file's directory, like the constructor reads it.
    module = {"type": "piper", "model": "voice.onnx", "base_dir": str(tmp_path)}
    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"onnx")

    items = PiperModule.check_files(module)

    assert [(i["id"], i["status"], i["preparable"]) for i in items] == [
        ("piper:model", "ready", False),
        ("piper:config", "missing", False),
    ]
    assert str(tmp_path / "voice.onnx.json") in items[1]["detail"]

    PiperModule.prepare(module)  # nothing it can fetch
    assert hub.downloads == []

    (tmp_path / "voice.onnx.json").write_text("{}", encoding="utf-8")
    assert [i["status"] for i in PiperModule.check_files(module)] == ["ready", "ready"]


def test_without_the_extra_the_hub_files_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)

    items = PiperModule.check_files({"type": "piper"})

    assert [(i["status"], i["preparable"]) for i in items] == [("unknown", True)] * 2
    assert "piper extra" in items[0]["detail"]
    with pytest.raises(ConfigError, match=r"pip install tts-engine\[piper\]"):
        PiperModule.prepare({"type": "piper"})


def test_a_failed_download_is_a_tts_error(hub: _Hub) -> None:
    hub.fail = OSError("no route to host")

    with pytest.raises(TTSError, match="piper download failed: no route to host"):
        PiperModule.prepare({"type": "piper"})


def test_items_refuse_a_config_the_constructor_refuses(hub: _Hub) -> None:
    with pytest.raises(ConfigError, match="'voice' or 'model', not both"):
        PiperModule.check_files({"type": "piper", "voice": "a-b-c", "model": "x.onnx"})


@pytest.mark.parametrize(
    ("module", "path"),
    [
        ({"type": "piper", "speed": 9}, "/module/speed"),
        ({"type": "piper", "speaker": -1}, "/module/speaker"),
        ({"type": "piper", "speaker": True}, "/module/speaker"),
        ({"type": "piper", "threads": 1.5}, "/module/threads"),
        ({"type": "piper", "voice": "ljspeech"}, "/module"),
        ({"type": "piper", "model": "hf://only-a-repo"}, "/module"),
        ({"type": "piper", "voice": "a-b-c", "model": "x.onnx"}, "/module"),
    ],
)
def test_validate_and_the_constructor_refuse_the_same_config_with_the_same_message(
    module: dict[str, Any], path: str
) -> None:
    problems = setup.check_config({"module": module})

    assert [problem["path"] for problem in problems] == [path]
    with pytest.raises(ConfigError) as error:
        PiperModule(module)
    assert str(error.value) == problems[0]["message"]


@pytest.mark.parametrize("speaker", [0, 12, "p225", None])
def test_a_speaker_is_a_name_or_an_index(speaker: Any) -> None:
    module = {"type": "piper", "speaker": speaker}

    assert setup.check_config({"module": module}) == []
    assert PiperModule.validated(module)["speaker"] == speaker
