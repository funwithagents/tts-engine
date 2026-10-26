"""Unit tests for PocketModule with the pocket-tts library faked.

The fast tier must run without the `pocket` extra (no `torch`), so `torch` and
`pocket_tts` are faked via `sys.modules`. Config-validation tests need no fake —
validation runs before the lazy import.
"""

import asyncio
import sys
import time
import types
from pathlib import Path

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
        self.raise_on_voice: Exception | None = None
        self.endless = False
        self.texts: list[str] = []
        self.stops: list = []

    def to(self, device):
        self.moved_to = device
        return self

    def get_state_for_audio_prompt(self, voice):
        self.voice = voice
        if self.raise_on_voice is not None:
            raise self.raise_on_voice
        return {"voice": voice}

    def generate_audio_stream(self, state, text, *, stop=None, **kwargs):
        self.stream_kwargs = kwargs
        self.stops.append(stop)
        self.texts.append(text)
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        if self.endless:
            # Like the library: generation ends once the stop event is set.
            while stop is None or not stop.is_set():
                time.sleep(0.005)
                yield _FakeChunk([0.1])
            return
        yield from self.chunks


class _FakeTTSModel:
    """Fake `pocket_tts.TTSModel`; `load_model` returns a shared `_FakeModel`."""

    instance = _FakeModel()
    raise_on_load: Exception | None = None

    @classmethod
    def load_model(cls, **kwargs):
        cls.instance.load_kwargs = kwargs
        if cls.raise_on_load is not None:
            raise cls.raise_on_load
        return cls.instance


def _default_voice(language, config=None, checkpoint=None):
    """Fake `get_default_voice_for_language`: echoes what it was asked for."""
    if config is not None:
        return "custom-default"
    return "alba" if language is None else f"default-{language}"


def _install_fakes(monkeypatch, *, cuda: bool = False, pocket: bool = True) -> None:
    # SimpleNamespace (not ModuleType) so arbitrary attrs assign cleanly; the
    # import machinery returns whatever object sits in sys.modules.
    torch_mod = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda)
    )
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    if pocket:
        defaults_mod = types.SimpleNamespace(
            get_default_voice_for_language=_default_voice
        )
        pocket_mod = types.SimpleNamespace(
            TTSModel=_FakeTTSModel, default_parameters=defaults_mod
        )
        monkeypatch.setitem(sys.modules, "pocket_tts", pocket_mod)
        monkeypatch.setitem(sys.modules, "pocket_tts.default_parameters", defaults_mod)
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


@pytest.mark.parametrize("device", ["gpu", [], 1])
def test_invalid_device_raises(device):
    with pytest.raises(ConfigError, match="device"):
        PocketModule({"type": "pocket", "device": device})


@pytest.mark.parametrize("bad", ["", 7])
def test_invalid_config_raises(bad):
    with pytest.raises(ConfigError, match="config"):
        PocketModule({"type": "pocket", "config": bad})


def test_language_and_config_together_raise():
    with pytest.raises(ConfigError, match="not both"):
        PocketModule({"type": "pocket", "language": "french", "config": "m.yaml"})


@pytest.mark.parametrize("bad", [1, "yes", None])
def test_invalid_quantize_raises(bad):
    with pytest.raises(ConfigError, match="quantize"):
        PocketModule({"type": "pocket", "quantize": bad})


@pytest.mark.parametrize("device", ["cuda", "mps"])
def test_quantize_on_a_gpu_device_raises(device):
    with pytest.raises(ConfigError, match="CPU-only"):
        PocketModule({"type": "pocket", "quantize": True, "device": device})


@pytest.mark.parametrize("bad", [-0.1, True, "0.3"])
def test_invalid_temperature_raises(bad):
    with pytest.raises(ConfigError, match="temperature"):
        PocketModule({"type": "pocket", "temperature": bad})


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


def test_device_auto_resolves_to_cpu_under_quantize(monkeypatch, fresh_model):
    _install_fakes(monkeypatch, cuda=True)
    PocketModule({"type": "pocket", "quantize": True})
    assert fresh_model.moved_to == "cpu"
    assert fresh_model.load_kwargs == {"quantize": True}


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


@pytest.mark.parametrize("name", ["me.wav", "me.mp3", "me.flac", "me.safetensors"])
def test_voice_file_resolves_against_base_dir(monkeypatch, fresh_model, name):
    _install_fakes(monkeypatch)
    PocketModule({"type": "pocket", "voice": name, "base_dir": "/some/dir"})
    # Platform-native separators: resolve_path joins with pathlib.
    assert fresh_model.voice == str(Path("/some/dir") / name)


def test_voice_url_passed_through(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    url = "hf://kyutai/tts-voices/alba-mackenna/casual.wav"
    PocketModule({"type": "pocket", "voice": url, "base_dir": "/some/dir"})
    assert fresh_model.voice == url


def test_default_voice_follows_the_language(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    PocketModule({"type": "pocket", "language": "french"})
    assert fresh_model.voice == "default-french"


def test_local_config_resolves_and_picks_the_custom_default_voice(
    monkeypatch, fresh_model
):
    _install_fakes(monkeypatch)
    PocketModule({"type": "pocket", "config": "m.yaml", "base_dir": "/some/dir"})
    assert fresh_model.load_kwargs == {"config": str(Path("/some/dir") / "m.yaml")}
    assert fresh_model.voice == "custom-default"


def test_url_config_and_temperature_forwarded(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    PocketModule(
        {"type": "pocket", "config": "hf://me/repo/m.yaml@v1", "temperature": 0.5}
    )
    assert fresh_model.load_kwargs == {"config": "hf://me/repo/m.yaml@v1", "temp": 0.5}


@pytest.mark.parametrize("exc", [ValueError("no cloning"), FileNotFoundError("x")])
def test_library_rejection_at_load_is_a_config_error(monkeypatch, fresh_model, exc):
    _install_fakes(monkeypatch)
    fresh_model.raise_on_voice = exc
    with pytest.raises(ConfigError, match=str(exc)):
        PocketModule({"type": "pocket"})


def test_other_load_failure_is_a_tts_error(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    monkeypatch.setattr(_FakeTTSModel, "raise_on_load", RuntimeError("hub down"))
    with pytest.raises(TTSError, match="hub down"):
        PocketModule({"type": "pocket"})


def test_language_and_max_tokens_forwarded(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule(
        {"type": "pocket", "voice": "marius", "language": "fr", "max_tokens": 200}
    )
    import asyncio

    asyncio.run(module.synthesize_text("hello", TTSOptions(), lambda _b: None))
    assert fresh_model.voice == "marius"
    assert fresh_model.load_kwargs == {"language": "fr"}
    assert fresh_model.stream_kwargs == {"max_tokens": 200}
    assert fresh_model.stops[0] is not None


# --- streaming ---------------------------------------------------------------


def test_stream_produces_int16_pcm(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})

    received = bytearray()
    import asyncio

    asyncio.run(module.synthesize_text("hi", TTSOptions(), received.extend))

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
        asyncio.run(module.synthesize_text("hi", TTSOptions(), lambda _b: None))


def test_stream_does_not_mask_callback_errors(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})

    def bad_callback(_b):
        raise ValueError("playback failed")

    import asyncio

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.synthesize_text("hi", TTSOptions(), bad_callback))


def test_callback_failure_stops_the_library_generation(monkeypatch, fresh_model):
    # A sink failure ends the utterance: the library's generation thread must
    # be told to stop (as on cancel), not left running the sentence out in the
    # background while the model is reused for the next utterance.
    _install_fakes(monkeypatch)
    fresh_model.endless = True
    module = PocketModule({"type": "pocket"})

    def bad_callback(_b):
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.synthesize_text("hi", TTSOptions(), bad_callback))
    assert fresh_model.stops[0].is_set()


async def test_cancel_stops_callbacks_before_stream_raises(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    fresh_model.endless = True
    module = PocketModule({"type": "pocket"})

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    task = asyncio.create_task(module.synthesize_text("hi", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
    assert fresh_model.stops[0].is_set()  # the library's generation was told too


def _signal(loop: asyncio.AbstractEventLoop, event: asyncio.Event):
    """A callback that sets `event` on `loop` from the module's worker thread."""

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(event.set)

    return callback


# --- text stream -------------------------------------------------------------


def test_text_stream_runs_inference_per_sentence(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})

    async def source():
        for piece in ["Hello wor", "ld. How are", " you? Fine"]:
            yield piece

    received = bytearray()
    asyncio.run(module.synthesize_text_stream(source(), TTSOptions(), received.extend))

    assert fresh_model.texts == ["Hello world.", "How are you?", "Fine"]
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake generation


async def test_text_stream_feeds_first_sentence_before_the_source_finishes(
    monkeypatch, fresh_model
):
    _install_fakes(monkeypatch)
    module = PocketModule({"type": "pocket"})
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello world. "
        await got_audio.wait()
        yield "Second one."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), _signal(loop, got_audio)),
        timeout=5,
    )
    assert fresh_model.texts == ["Hello world.", "Second one."]


async def test_text_stream_cancel_mid_audio_stops_callbacks_before_raising(
    monkeypatch, fresh_model
):
    _install_fakes(monkeypatch)
    fresh_model.endless = True  # the first sentence's audio never ends
    module = PocketModule({"type": "pocket"})
    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    async def source():
        yield "Hello world. "  # a complete sentence: inference starts
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
    assert fresh_model.texts == ["Hello world."]
    assert fresh_model.stops[0].is_set()
