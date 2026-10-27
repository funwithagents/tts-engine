"""Unit tests for PhononModule with the ptts runtime faked.

The fast tier never loads a real model or touches the Hugging Face Hub, so
`ptts` is faked via `sys.modules`. Config-validation tests need no fake —
validation runs before the lazy import.
"""

import asyncio
import gc
import sys
import threading
import types
import weakref
from pathlib import Path

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions, float_to_int16_pcm
from tts_engine.modules.phonon import PhononModule


class _FakeAudioStream:
    """Stands in for `ptts.AudioStream`: a context manager iterating float32 chunks."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self._chunks = iter(backend.chunks)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self.closed = True
        if self._backend.raise_on_close is not None:
            raise self._backend.raise_on_close

    def __iter__(self):
        return self

    def __next__(self):
        backend = self._backend
        if self.closed:
            raise StopIteration
        if backend.raise_on_next is not None:
            raise backend.raise_on_next
        if backend.block is not None:
            # Endless stream that waits to be released after each chunk.
            backend.reached.set()
            backend.block.wait(timeout=5)
            return np.asarray([0.1], dtype=np.float32)
        return next(self._chunks)


class _FakeBackend:
    """Shared state behind the fake `ptts.TTS`: records calls, injects faults."""

    def __init__(self) -> None:
        self.init_kwargs: dict | None = None
        self.added_voices: list[tuple[str, str]] = []
        self.raise_on_init: Exception | None = None
        self.raise_on_stream: Exception | None = None
        self.raise_on_next: Exception | None = None
        self.raise_on_close: Exception | None = None
        self.chunks = [
            np.asarray([0.0, 0.5, -0.5], dtype=np.float32),
            np.asarray([1.5, -2.0], dtype=np.float32),  # out of range: clipped
        ]
        self.texts: list[str] = []
        self.voices_used: list[str | None] = []
        self.streams: list[_FakeAudioStream] = []
        self.instances: list = []
        self.block: threading.Event | None = None
        self.reached = threading.Event()


def _fake_ptts(backend: _FakeBackend) -> types.SimpleNamespace:
    class FakeTTS:
        sample_rate = 24000
        device = "cpu"
        quant = "f32"

        def __init__(self, **kwargs) -> None:
            backend.init_kwargs = kwargs
            backend.instances.append(self)
            if backend.raise_on_init is not None:
                raise backend.raise_on_init

        def add_voice(self, name: str, path: str) -> None:
            backend.added_voices.append((name, path))

        def stream(self, text: str, *, voice=None):
            backend.texts.append(text)
            backend.voices_used.append(voice)
            if backend.raise_on_stream is not None:
                raise backend.raise_on_stream
            audio = _FakeAudioStream(backend)
            backend.streams.append(audio)
            return audio

    return types.SimpleNamespace(TTS=FakeTTS)


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()
    monkeypatch.setitem(sys.modules, "ptts", _fake_ptts(backend))
    return backend


def _run(coro):
    return asyncio.run(coro)


# --- config validation (no fake needed: runs before the lazy import) ---------


@pytest.mark.parametrize(
    "config, match",
    [
        ({"language": "english"}, "language"),
        ({"language": ""}, "language"),
        ({"language": 1}, "language"),
        ({"language": ["en"]}, "language"),
        ({"language": None}, "language"),
        ({"model": ""}, "model"),
        ({"voice": ""}, "voice"),
        ({"quant": ""}, "quant"),
        ({"device": ""}, "device"),
        ({"device": None}, "device"),
        ({"device": 0}, "device"),
        ({"temperature": -0.1}, "temperature"),
        ({"cfg_coef": "2"}, "cfg_coef"),
    ],
)
def test_invalid_config_raises(config, match):
    with pytest.raises(ConfigError, match=match):
        PhononModule({"type": "phonon", **config})


# --- construction ------------------------------------------------------------


@pytest.mark.parametrize(
    "config, init_kwargs",
    [
        ({}, {"lang": "en"}),
        ({"language": "none"}, {"lang": None}),
        (
            {
                "language": "fr",
                "model": "hf://gradium/phonon-40m-fr",
                "voice": "Juniper",
                "device": "cpu",
                "quant": "q8_0",
                "temperature": 0,
                "cfg_coef": 2,
            },
            {
                "lang": "fr",
                "model": "hf://gradium/phonon-40m-fr",
                "voice": "Juniper",
                "device": "cpu",
                "quant": "q8_0",
                "temperature": 0.0,
                "cfg_coef": 2.0,
            },
        ),
    ],
    ids=["defaults-pass-only-english", "language-none", "set-fields-forwarded"],
)
def test_init_kwargs(fake, config, init_kwargs):
    module = PhononModule({"type": "phonon", **config})
    assert fake.init_kwargs == init_kwargs
    assert module.sample_rate == 24000


def test_local_model_resolves_against_base_dir(fake):
    PhononModule({"type": "phonon", "model": "models/phonon", "base_dir": "/cfg"})
    assert fake.init_kwargs is not None
    assert fake.init_kwargs["model"] == str(Path("/cfg") / "models/phonon")


def test_safetensors_voice_is_registered_not_passed(fake):
    module = PhononModule(
        {"type": "phonon", "voice": "voices/me.safetensors", "base_dir": "/cfg"}
    )
    assert fake.init_kwargs == {"lang": "en"}
    assert fake.added_voices == [("me", str(Path("/cfg") / "voices/me.safetensors"))]

    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.voices_used == ["me"]


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("unsupported language code"),
        NotImplementedError("device 'cuda' is not available"),
        LookupError("no voice 'nobody' is registered"),
        FileNotFoundError("no such file"),
    ],
)
def test_library_rejection_is_config_error(fake, exc):
    fake.raise_on_init = exc
    with pytest.raises(ConfigError, match=str(exc)):
        PhononModule({"type": "phonon"})


def test_other_load_failure_is_tts_error(fake):
    fake.raise_on_init = OSError("cannot fetch config.json")
    with pytest.raises(TTSError, match="model load failed"):
        PhononModule({"type": "phonon"})


# --- synthesize_text ---------------------------------------------------------


def test_synthesize_text_feeds_int16_pcm(fake):
    module = PhononModule({"type": "phonon", "voice": "Harper"})
    received = bytearray()
    _run(module.synthesize_text("Hello.", TTSOptions(), received.extend))

    # The runtime's float chunks, converted and fed in order (the conversion's
    # values are float_to_int16_pcm's own test).
    assert bytes(received) == b"".join(float_to_int16_pcm(c) for c in fake.chunks)
    assert fake.texts == ["Hello."]
    assert fake.voices_used == ["Harper"]
    assert fake.streams[0].closed


def test_stream_creation_error_is_tts_error(fake):
    module = PhononModule({"type": "phonon"})
    fake.raise_on_stream = ValueError("nothing to synthesize")
    with pytest.raises(TTSError, match="nothing to synthesize"):
        _run(module.synthesize_text("...", TTSOptions(), lambda _b: None))


def test_iteration_error_is_tts_error_and_closes(fake):
    module = PhononModule({"type": "phonon"})
    fake.raise_on_next = RuntimeError("decoder boom")
    with pytest.raises(TTSError, match="decoder boom"):
        _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.streams[0].closed


def test_callback_error_wins_over_a_failing_close(fake):
    module = PhononModule({"type": "phonon"})
    fake.raise_on_close = RuntimeError("decoder teardown failed")

    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _run(module.synthesize_text("Hi.", TTSOptions(), bad_callback))
    assert fake.streams[0].closed


async def test_cancel_closes_the_stream_before_cancelled_error(fake):
    module = PhononModule({"type": "phonon"})
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending chunk return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake.streams[0].closed


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_synthesizes_per_sentence_before_the_source_finishes(fake):
    # Sentences split across pieces, and a source that only goes on once the
    # first sentence's audio is fed: a module that waited for the whole text
    # would deadlock here.
    module = PhononModule({"type": "phonon"})
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()
    received = bytearray()

    def callback(chunk: bytes) -> None:
        received.extend(chunk)
        loop.call_soon_threadsafe(got_audio.set)

    async def source():
        yield "Hello wor"
        yield "ld. How are"
        await got_audio.wait()
        yield " you? Fine"

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    assert fake.texts == ["Hello world.", "How are you?", "Fine"]
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake stream
    assert all(stream.closed for stream in fake.streams)


async def test_text_stream_cancel_closes_the_stream_before_cancelled_error(fake):
    module = PhononModule({"type": "phonon"})
    fake.block = threading.Event()

    async def source():
        yield "Hi. "  # a complete sentence: its stream opens
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending chunk return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert fake.streams[0].closed
    assert fake.texts == ["Hi."]


def test_close_drops_the_runtime(fake):
    module = PhononModule({"type": "phonon"})
    runtime = weakref.ref(fake.instances.pop())
    gc.collect()
    assert runtime() is not None  # the module holds it until closed

    module.close()
    gc.collect()
    assert runtime() is None
