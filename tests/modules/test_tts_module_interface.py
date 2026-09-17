from pathlib import Path

import pytest

from tts_engine.config import ConfigError
from tts_engine.modules import REGISTRY, load_module
from tts_engine.modules.base import TTSModule, TTSOptions, resolve_path


def test_load_module_unknown_type_raises():
    with pytest.raises(ConfigError):
        load_module({"type": "nonexistent_xyz"})


def test_load_module_registered_type():
    class StubModule(TTSModule):
        def __init__(self, config: dict):
            self.config = config

        @property
        def sample_rate(self) -> int:
            return 44100

        async def stream(self, text, options, callback):
            pass

    REGISTRY["_stub_test"] = StubModule
    try:
        result = load_module({"type": "_stub_test", "extra": "value"})
        assert isinstance(result, StubModule)
        assert result.config == {"type": "_stub_test", "extra": "value"}
    finally:
        del REGISTRY["_stub_test"]


def test_tts_options_default_instantiation():
    TTSOptions()  # must not raise


# --- resolve_path ------------------------------------------------------------


def test_resolve_path_absolute_returned_unchanged(tmp_path):
    absolute = tmp_path / "voice.wav"
    assert resolve_path({"base_dir": "/other"}, str(absolute)) == absolute


def test_resolve_path_relative_joins_base_dir():
    config = {"type": "x", "base_dir": "/some/dir"}
    assert resolve_path(config, "sub/a.wav") == Path("/some/dir/sub/a.wav")


def test_resolve_path_relative_without_base_dir_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_path({"type": "x"}, "a.wav") == Path.cwd() / "a.wav"
