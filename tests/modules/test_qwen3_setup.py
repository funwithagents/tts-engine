"""What a qwen3 config needs on the machine (`setup.check` / `setup.prepare`),
with `qwentts_cpp` and the Hugging Face cache faked: no model is loaded,
nothing is downloaded."""

import sys
import types
from typing import Any

import pytest

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError

_REPO = "Serveurperso/Qwen3-TTS-GGUF"
_DEFAULT = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_VOICE_DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
_STEMS = {
    _DEFAULT: "qwen-talker-0.6b-customvoice",
    _VOICE_DESIGN: "qwen-talker-1.7b-voicedesign",
}


class _FakeLibrary:
    """Stands in for `qwentts_cpp`'s file resolution over a fake cache: the
    names it derives are the library's, a download adds them to the cache."""

    def __init__(self, *, private_names: bool = True) -> None:
        self.cache: set[str] = set()
        self.downloads: list[tuple[str, str]] = []
        self.loads = 0
        self.raise_on_download: Exception | None = None
        self._private_names = private_names

    @staticmethod
    def _quant(quant: str) -> str:
        names = {"Q8_0": "Q8_0", "Q8": "Q8_0", "F32": "F32", "Q4_K_M": "Q4_K_M"}
        if quant.upper() not in names:
            raise ValueError(f"Unsupported qwentts quant {quant!r}.")
        return names[quant.upper()]

    def _resolve(
        self, model_id: str, *, quant: str = "BF16", local_files_only: bool = False
    ) -> tuple[str, str]:
        if model_id not in _STEMS:
            raise ValueError(f"Unsupported Qwen3-TTS model id {model_id!r}.")
        name = self._quant(quant)
        filenames = (
            f"{_STEMS[model_id]}-{name}.gguf",
            f"qwen-tokenizer-12hz-{name}.gguf",
        )
        if local_files_only:
            for filename in filenames:
                if filename not in self.cache:
                    raise FileNotFoundError(f"{filename} is not cached")
        else:
            if self.raise_on_download is not None:
                raise self.raise_on_download
            self.downloads.append((model_id, quant))
            self.cache.update(filenames)
        talker, codec = (f"/cache/{filename}" for filename in filenames)
        return talker, codec

    def module(self) -> types.SimpleNamespace:
        fake = self

        class FakeQwenTTS:
            @classmethod
            def from_pretrained(cls, *args: Any, **kwargs: Any) -> "FakeQwenTTS":
                fake.loads += 1
                return cls()

        models = types.SimpleNamespace(GGUF_REPO=_REPO)
        if self._private_names:
            models._MODEL_TO_TALKER_STEM = dict(_STEMS)
            models._normalize_quant = self._quant
        return types.SimpleNamespace(
            QwenTTS=FakeQwenTTS, resolve_gguf_paths=self._resolve, models=models
        )

    def cached(
        self, repo_id: str, filename: str, revision: str | None = None
    ) -> str | None:
        assert repo_id == _REPO
        return f"/cache/{filename}" if filename in self.cache else None


def _install(monkeypatch: pytest.MonkeyPatch, library: _FakeLibrary) -> _FakeLibrary:
    huggingface_hub = pytest.importorskip("huggingface_hub")
    monkeypatch.setitem(sys.modules, "qwentts_cpp", library.module())
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", library.cached)
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda *a, **k: pytest.fail("the library's own download is the one used"),
    )
    return library


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> _FakeLibrary:
    return _install(monkeypatch, _FakeLibrary())


def _status(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(item["id"], item["status"]) for item in items]


def test_the_two_files_are_missing_until_prepared(library: _FakeLibrary) -> None:
    config = {"module": {"type": "qwen3"}}

    before = setup.check(config)
    assert _status(before) == [("qwen3:talker", "missing"), ("qwen3:codec", "missing")]
    assert all(item["preparable"] for item in before)
    assert "qwen-talker-0.6b-customvoice-Q8_0.gguf" in before[0]["detail"]
    assert "qwen-tokenizer-12hz-Q8_0.gguf" in before[1]["detail"]

    progress: list[dict[str, Any]] = []
    setup.prepare(config, progress.append)

    assert library.downloads == [(_DEFAULT, "Q8_0")]
    assert [p["id"] for p in progress if p["fraction"] == 1.0] == [
        "qwen3:talker",
        "qwen3:codec",
    ]
    after = setup.check(config)
    assert _status(after) == [("qwen3:talker", "ready"), ("qwen3:codec", "ready")]
    assert after[0]["detail"] == "/cache/qwen-talker-0.6b-customvoice-Q8_0.gguf"

    setup.prepare(config)  # everything is ready: nothing more is fetched
    assert len(library.downloads) == 1
    assert library.loads == 0  # checking and preparing never load the model


def test_the_files_follow_the_model_and_the_weight_format(
    library: _FakeLibrary,
) -> None:
    library.cache = {
        "qwen-talker-0.6b-customvoice-Q8_0.gguf",
        "qwen-tokenizer-12hz-Q8_0.gguf",
        "qwen-tokenizer-12hz-F32.gguf",
    }
    module = {
        "type": "qwen3",
        "model": _VOICE_DESIGN,
        "instruct": "A calm narrator.",
        "quant": "F32",
    }

    items = setup.check({"module": module})

    # Another model's talker does not count; the codec is shared by a format.
    assert _status(items) == [("qwen3:talker", "missing"), ("qwen3:codec", "ready")]
    assert "qwen-talker-1.7b-voicedesign-F32.gguf" in items[0]["detail"]

    progress: list[dict[str, Any]] = []
    setup.prepare({"module": module}, progress.append)
    assert library.downloads == [(_VOICE_DESIGN, "F32")]
    assert {p["id"] for p in progress} == {"qwen3:talker"}


def test_a_library_that_hides_its_file_names_still_says_missing_then_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _install(monkeypatch, _FakeLibrary(private_names=False))
    config = {"module": {"type": "qwen3"}}

    before = setup.check(config)
    assert [(i["id"], i["status"], i["preparable"]) for i in before] == [
        ("qwen3:model", "missing", True)
    ]

    setup.prepare(config)

    assert library.downloads == [(_DEFAULT, "Q8_0")]
    assert _status(setup.check(config)) == [
        ("qwen3:talker", "ready"),
        ("qwen3:codec", "ready"),
    ]


def test_a_model_or_format_the_library_does_not_know_is_a_config_error(
    library: _FakeLibrary,
) -> None:
    with pytest.raises(ConfigError, match="Qwen3 module: Unsupported qwentts quant"):
        setup.check({"module": {"type": "qwen3", "quant": "Q9"}})
    with pytest.raises(ConfigError, match="Qwen3 module: Unsupported Qwen3-TTS model"):
        setup.check({"module": {"type": "qwen3", "model": "Qwen/Other"}})
    # A combination the module refuses on its own never reaches the library.
    with pytest.raises(ConfigError, match="'instruct' is required"):
        setup.check({"module": {"type": "qwen3", "model": _VOICE_DESIGN}})
    assert library.downloads == []


def test_a_failed_download_is_a_tts_error(library: _FakeLibrary) -> None:
    library.raise_on_download = OSError("hub unreachable")

    with pytest.raises(TTSError, match="qwen3 download failed: hub unreachable"):
        setup.prepare({"module": {"type": "qwen3"}})


def test_without_the_extra_the_model_is_unknown_and_prepare_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "qwentts_cpp", None)  # as off Apple silicon
    config = {"module": {"type": "qwen3"}}

    items = setup.check(config)

    assert [(i["id"], i["status"], i["preparable"]) for i in items] == [
        ("qwen3:model", "unknown", True)
    ]
    assert "qwen3 extra is not installed" in items[0]["detail"]
    with pytest.raises(ConfigError, match=r"pip install tts-engine\[qwen3\]"):
        setup.prepare(config)
