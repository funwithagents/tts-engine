"""What a pocket config needs on the machine (specs/setup.md, "`check` and
`prepare`"), with pocket-tts, its packaged model configs and the Hugging Face
cache faked: no torch, no model, no network."""

import hashlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError
from tts_engine.modules.pocket import PocketModule


def _files(config: dict) -> list[dict]:
    """The module's file items (`check_files`), with its library faked."""
    from tts_engine.modules import REGISTRY

    return REGISTRY[config["module"]["type"]].check_files(config["module"])


_GATED = "hf://kyutai/pocket-tts/languages/{}/model.safetensors@aaa"
_PUBLIC = "hf://kyutai/pocket-public/languages/{}/model.safetensors@bbb"
_TOKENIZER = "hf://kyutai/pocket-public/languages/{}/tokenizer.json@ccc"
_PRESET = "hf://kyutai/pocket-public/languages/{}/embeddings/{}.safetensors@ddd"
_STATUSES = ["model", "tokenizer", "voice"]


class _Library:
    """A fake pocket-tts: packaged configs for english and french, a preset
    table, and a `download_if_necessary` that fills the fake caches."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.cached: set[str] = set()
        self.downloads: list[str] = []
        self.gated_refused = False
        self.offline = False
        self.loads: list[dict[str, Any]] = []
        self.home = tmp_path / "home"
        configs = tmp_path / "configs"
        configs.mkdir()
        for language in ("english", "french"):
            (configs / f"{language}.yaml").write_text("", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: self.home)

        def module(name: str, **members: Any) -> None:
            fake: Any = types.ModuleType(name)
            fake.__dict__.update(members)
            monkeypatch.setitem(sys.modules, name, fake)

        module("huggingface_hub", try_to_load_from_cache=self._hub_cache)
        module("torch", cuda=SimpleNamespace(is_available=lambda: False))
        module("pocket_tts", TTSModel=SimpleNamespace(load_model=self._load_model))
        module(
            "pocket_tts.default_parameters",
            DEFAULT_LANGUAGE="english",
            get_default_voice_for_language=lambda language, config=None: (
                "estelle" if language == "french" else "alba"
            ),
        )
        module("pocket_tts.utils")
        module(
            "pocket_tts.utils.utils",
            _ORIGINS_OF_PREDEFINED_VOICES={"alba": "a.wav", "estelle": "e.wav"},
            get_predefined_voice=lambda language, name: _PRESET.format(language, name),
            download_if_necessary=self._download,
        )
        module(
            "pocket_tts.utils.config",
            CONFIGS_DIR=configs,
            load_config=lambda path: SimpleNamespace(
                weights_path=_GATED.format(Path(path).stem),
                weights_path_without_voice_cloning=_PUBLIC.format(Path(path).stem),
                flow_lm=SimpleNamespace(
                    weights_path=None,
                    lookup_table=SimpleNamespace(
                        tokenizer_path=_TOKENIZER.format(Path(path).stem)
                    ),
                ),
                mimi=SimpleNamespace(weights_path=None),
            ),
        )

    def _hub_cache(
        self, repo_id: str, filename: str, revision: str | None = None
    ) -> str | None:
        source = f"hf://{repo_id}/{filename}" + (f"@{revision}" if revision else "")
        return f"/hub/{filename}" if source in self.cached else None

    def _download(self, source: str) -> Path:
        if self.offline:
            raise OSError("no route to host")
        if self.gated_refused and "kyutai/pocket-tts/" in source:
            raise PermissionError("gated repository")
        self.downloads.append(source)
        if source.startswith("https://"):
            suffix = Path(source).suffix
            name = hashlib.sha256(source.encode()).hexdigest() + suffix
            path = self.home / ".cache" / "pocket_tts" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            return path
        self.cached.add(source)
        return Path("/hub/file")

    def _load_model(self, **kwargs: Any) -> Any:
        self.loads.append(kwargs)
        model = SimpleNamespace(
            sample_rate=24000, get_state_for_audio_prompt=lambda voice: {}
        )
        model.to = lambda device: model
        return model


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Library:
    return _Library(monkeypatch, tmp_path)


def _statuses(module: dict[str, Any]) -> list[str]:
    items = PocketModule.check_files(module)
    assert [item["id"] for item in items] == [f"pocket:{key}" for key in _STATUSES]
    return [item["status"] for item in items]


def test_the_default_model_s_files_are_missing_until_prepared(
    library: _Library,
) -> None:
    config = {"module": {"type": "pocket"}}

    before = _files(config)
    assert [(item["id"], item["status"]) for item in before] == [
        ("pocket:model", "missing"),
        ("pocket:tokenizer", "missing"),
        ("pocket:voice", "missing"),
    ]
    assert all(item["preparable"] for item in before)

    progress: list[dict[str, Any]] = []
    setup.prepare(config, progress.append)

    # The voice-cloning weights when they can be had, the tokenizer, and the
    # default voice's state precomputed for this model.
    assert library.downloads == [
        _GATED.format("english"),
        _TOKENIZER.format("english"),
        _PRESET.format("english", "alba"),
    ]
    assert [p["id"] for p in progress if p["fraction"] == 1.0] == [
        "pocket:model",
        "pocket:tokenizer",
        "pocket:voice",
    ]
    assert [item["status"] for item in _files(config)] == ["ready"] * 3
    assert library.loads == []  # fetched file by file: no model was loaded

    setup.prepare(config)  # everything is ready: nothing more is fetched
    assert len(library.downloads) == 3


def test_the_public_weights_stand_in_when_the_gated_ones_are_refused(
    library: _Library,
) -> None:
    library.gated_refused = True
    module = {"type": "pocket", "language": "french"}

    PocketModule.prepare(module)

    assert library.downloads == [
        _PUBLIC.format("french"),
        _TOKENIZER.format("french"),
        _PRESET.format("french", "estelle"),
    ]
    assert _statuses(module) == ["ready", "ready", "ready"]


def test_a_cloned_voice_needs_the_gated_weights(library: _Library) -> None:
    url = "https://example.test/voices/me.wav"
    module = {"type": "pocket", "voice": url}
    library.cached = {_PUBLIC.format("english"), _TOKENIZER.format("english")}

    # The public weights cannot clone: cached, they do not make the model ready.
    assert _statuses(module) == ["missing", "ready", "missing"]

    library.gated_refused = True
    with pytest.raises(TTSError, match="pocket-tts download failed: gated repository"):
        PocketModule.prepare(module)

    library.gated_refused = False
    PocketModule.prepare(module)
    assert library.downloads == [_GATED.format("english"), url]
    assert _statuses(module) == ["ready", "ready", "ready"]


def test_a_voice_state_url_is_looked_up_in_the_hub_cache_as_it_is(
    library: _Library,
) -> None:
    voice = "hf://me/voices/mine.safetensors@abc"
    module = {"type": "pocket", "voice": voice}
    library.cached = {
        _PUBLIC.format("english"),
        _TOKENIZER.format("english"),
        voice,
    }

    # A voice state works with either weights: nothing is missing.
    assert _statuses(module) == ["ready", "ready", "ready"]
    PocketModule.prepare(module)
    assert library.downloads == []


def test_a_local_voice_file_is_ready_or_missing_and_never_preparable(
    library: _Library, tmp_path: Path
) -> None:
    module = {"type": "pocket", "voice": "me.wav", "base_dir": str(tmp_path)}

    voice = PocketModule.check_files(module)[2]
    assert (voice["status"], voice["preparable"]) == ("missing", False)
    assert str(tmp_path / "me.wav") in voice["detail"]

    PocketModule.prepare(module)
    assert library.downloads == [_GATED.format("english"), _TOKENIZER.format("english")]

    (tmp_path / "me.wav").write_bytes(b"riff")
    assert _statuses(module) == ["ready", "ready", "ready"]


@pytest.mark.parametrize(
    "module",
    [
        {"type": "pocket", "config": "hf://me/model/custom.yaml"},
        {"type": "pocket", "language": "klingon"},
    ],
)
def test_a_model_whose_files_cannot_be_named_is_unknown_and_prepared_by_a_load(
    library: _Library, module: dict[str, Any]
) -> None:
    items = PocketModule.check_files(module)

    assert [(i["id"], i["status"], i["preparable"]) for i in items] == [
        ("pocket:model", "unknown", True)
    ]
    assert "first time" in items[0]["detail"]
    assert library.loads == []  # looking loads nothing

    progress: list[dict[str, Any]] = []
    PocketModule.prepare(module, progress.append)

    expected = {key: module[key] for key in ("config", "language") if key in module}
    assert library.loads == [expected]
    assert [p["fraction"] for p in progress] == [None, 1.0]


def test_a_library_laid_out_differently_is_unknown_not_an_error(
    library: _Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys.modules["pocket_tts.utils.config"],
        "load_config",
        lambda path: SimpleNamespace(weights=None),
    )

    assert [
        item["status"] for item in PocketModule.check_files({"type": "pocket"})
    ] == ["unknown"]


def test_without_the_extra_the_model_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("pocket_tts", "torch"):
        monkeypatch.setitem(sys.modules, name, None)

    items = PocketModule.check_files({"type": "pocket"})

    assert [(i["status"], i["preparable"]) for i in items] == [("unknown", True)]
    assert "pocket extra" in items[0]["detail"]
    with pytest.raises(ConfigError, match=r"pip install tts-engine\[pocket\]"):
        PocketModule.prepare({"type": "pocket"})


def test_a_failed_download_is_a_tts_error(library: _Library) -> None:
    library.offline = True

    with pytest.raises(TTSError, match="pocket-tts download failed: no route to host"):
        PocketModule.prepare({"type": "pocket"})


@pytest.mark.parametrize(
    ("module", "path"),
    [
        ({"type": "pocket", "device": "gpu"}, "/module/device"),
        ({"type": "pocket", "device": None}, "/module/device"),
        ({"type": "pocket", "quantize": "yes"}, "/module/quantize"),
        ({"type": "pocket", "temperature": -1}, "/module/temperature"),
        ({"type": "pocket", "max_tokens": 0}, "/module/max_tokens"),
        ({"type": "pocket", "voice": ""}, "/module/voice"),
        ({"type": "pocket", "voice": None}, "/module/voice"),
        ({"type": "pocket", "language": "english", "config": "m.yaml"}, "/module"),
        ({"type": "pocket", "quantize": True, "device": "mps"}, "/module"),
    ],
)
def test_validate_and_the_constructor_refuse_the_same_config_with_the_same_message(
    module: dict[str, Any], path: str
) -> None:
    problems = setup.check_config({"module": module})

    assert [problem["path"] for problem in problems] == [path]
    with pytest.raises(ConfigError) as error:
        PocketModule(module)
    assert str(error.value) == problems[0]["message"]
