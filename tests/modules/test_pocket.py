"""Unit tests for PocketModule with the pocket-tts library faked.

The fast tier must run without the `pocket` extra (no `torch`), so `torch` and
`pocket_tts` are faked via `sys.modules`. Config-validation tests need no fake —
validation runs before the lazy import.
"""

import sys
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.pocket import PocketModule


class _FakeChunk:
    """Stands in for a 1-D float32 torch tensor from generate_audio_stream."""

    def __init__(self, values) -> None:
        self._arr = np.asarray(values, dtype=np.float32)

    def detach(self):
        return self

    def to(self, device):
        return self

    def numpy(self):
        return self._arr


class _FakeModel:
    sample_rate = 24000

    def __init__(self) -> None:
        self.moved_to = None
        self.voice = None
        self.load_kwargs: dict = {}
        self.stream_kwargs: dict = {}
        self.chunks = [_FakeChunk([0.0, 0.5, -0.5, 1.0]), _FakeChunk([0.25])]
        self.raise_on_stream: Exception | None = None

    def to(self, device):
        self.moved_to = device
        return self

    def get_state_for_audio_prompt(self, voice):
        self.voice = voice
        return {"voice": voice}

    def generate_audio_stream(self, state, text, **kwargs):
        self.stream_kwargs = kwargs
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        yield from self.chunks


class _FakeTTSModel:
    """Fake `pocket_tts.TTSModel`; `load_model` returns a shared `_FakeModel`."""

    instance = _FakeModel()

    @classmethod
    def load_model(cls, **kwargs):
        cls.instance.load_kwargs = kwargs
        return cls.instance


def _install_fakes(monkeypatch, *, cuda: bool = False, pocket: bool = True) -> None:
    # SimpleNamespace (not ModuleType) so arbitrary attrs assign cleanly; the
    # import machinery returns whatever object sits in sys.modules.
    torch_mod = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda)
    )
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    if pocket:
        pocket_mod = types.SimpleNamespace(TTSModel=_FakeTTSModel)
        monkeypatch.setitem(sys.modules, "pocket_tts", pocket_mod)
    else:
        monkeypatch.setitem(sys.modules, "pocket_tts", None)  # -> ImportError


@pytest.fixture
def fresh_model(monkeypatch):
    """Give each test its own `_FakeModel` behind `_FakeTTSModel.load_model`."""
    model = _FakeModel()
    monkeypatch.setattr(_FakeTTSModel, "instance", model)
    return model


# --- config validation (no fake needed: runs before the lazy import) ---------


@pytest.mark.parametrize("bad", ["", 123, None])
def test_invalid_voice_raises(bad):
    with pytest.raises(ConfigError, match="voice"):
        PocketModule({"type": "pocket", "voice": bad})


def test_invalid_language_raises():
    with pytest.raises(ConfigError, match="language"):
        PocketModule({"type": "pocket", "language": ""})


def test_invalid_device_raises():
    with pytest.raises(ConfigError, match="device"):
        PocketModule({"type": "pocket", "device": "gpu"})


@pytest.mark.parametrize("bad", [0, -1, True, "5"])
def test_invalid_max_tokens_raises(bad):
    with pytest.raises(ConfigError, match="max_tokens"):
        PocketModule({"type": "pocket", "max_tokens": bad})


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    _install_fakes(monkeypatch, pocket=False)
    with pytest.raises(ConfigError, match=r"pocket extra"):
        PocketModule({"type": "pocket"})


# --- construction & device ---------------------------------------------------


def test_device_auto_resolves_to_cpu_without_cuda(monkeypatch, fresh_model):
    _install_fakes(monkeypatch, cuda=False)
    PocketModule({"type": "pocket"})
    assert fresh_model.moved_to == "cpu"


def test_device_auto_resolves_to_cuda_when_available(monkeypatch, fresh_model):
    _install_fakes(monkeypatch, cuda=True)
    PocketModule({"type": "pocket"})
    assert fresh_model.moved_to == "cuda"


def test_explicit_device_used_verbatim(monkeypatch, fresh_model):
    _install_fakes(monkeypatch, cuda=False)
    PocketModule({"type": "pocket", "device": "mps"})
    assert fresh_model.moved_to == "mps"


def test_defaults_and_forwarding(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})
    # default voice, model native rate, no language/max_tokens passed through
    assert fresh_model.voice == "alba"
    assert module.sample_rate == 24000
    assert fresh_model.load_kwargs == {}


def test_language_and_max_tokens_forwarded(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule(
        {"type": "pocket", "voice": "marius", "language": "fr", "max_tokens": 200}
    )
    import asyncio

    asyncio.run(module.stream("hello", TTSOptions(), lambda _b: None))
    assert fresh_model.voice == "marius"
    assert fresh_model.load_kwargs == {"language": "fr"}
    assert fresh_model.stream_kwargs == {"max_tokens": 200}


# --- streaming ---------------------------------------------------------------


def test_stream_produces_int16_pcm(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})

    received = bytearray()
    import asyncio

    asyncio.run(module.stream("hi", TTSOptions(), received.extend))

    assert len(received) % 2 == 0  # whole signed-16-bit samples
    samples = np.frombuffer(bytes(received), dtype="<i2")
    # [0.0, 0.5, -0.5, 1.0, 0.25] * 32767, truncated toward zero
    assert samples.tolist() == [0, 16383, -16383, 32767, 8191]


def test_stream_wraps_backend_error_in_tts_error(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    fresh_model.raise_on_stream = RuntimeError("inference boom")
    module = PocketModule({"type": "pocket"})

    import asyncio

    with pytest.raises(TTSError, match="inference boom"):
        asyncio.run(module.stream("hi", TTSOptions(), lambda _b: None))


def test_stream_does_not_mask_callback_errors(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})

    def bad_callback(_b):
        raise ValueError("playback failed")

    import asyncio

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.stream("hi", TTSOptions(), bad_callback))
