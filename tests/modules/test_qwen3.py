"""Unit tests for Qwen3Module with the qwentts_cpp bindings faked.

The fast tier never loads a real model, touches the Hugging Face Hub, or needs
the Apple-silicon wheel, so `qwentts_cpp` is faked via `sys.modules`.
Pre-load validation tests need no fake — it runs before the lazy import.
"""

import asyncio
import sys
import threading
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.qwen3 import Qwen3Module

_SPEAKERS = ["serena", "vivian", "uncle_fu", "ryan", "aiden"]
_LANGUAGES = ["chinese", "english", "french"]


class _FakeBackend:
    """Shared state behind the fake `qwentts_cpp.QwenTTS`: records calls,
    injects faults."""

    def __init__(self) -> None:
        self.load_args: tuple | None = None
        self.load_kwargs: dict | None = None
        self.raise_on_load: Exception | None = None
        self.raise_on_next: Exception | None = None
        self.raise_on_close: Exception | None = None
        self.model_type = "custom_voice"
        self.speakers = list(_SPEAKERS)
        self.rate = 24000
        self.chunks = [
            np.asarray([0.0, 0.5, -0.5], dtype=np.float32),
            np.asarray([1.5, -2.0], dtype=np.float32),  # out of range: clipped
        ]
        self.calls: list[dict] = []
        self.closed_streams: list[int] = []
        self.tts_closed = False
        self.block: threading.Event | None = None
        self.reached = threading.Event()


def _fake_qwentts_cpp(backend: _FakeBackend) -> types.SimpleNamespace:
    class FakeQwenTTSError(RuntimeError):
        pass

    class FakeQwenTTS:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            backend.load_args = args
            backend.load_kwargs = kwargs
            if backend.raise_on_load is not None:
                raise backend.raise_on_load
            return cls()

        def speaker_names(self) -> list[str]:
            return list(backend.speakers)

        def language_names(self) -> list[str]:
            return list(_LANGUAGES)

        def model_type(self) -> str:
            return backend.model_type

        def close(self) -> None:
            backend.tts_closed = True

        def stream(self, **kwargs):
            # A real generator, like the library's: arguments are only acted
            # on at the first `next`, and `close()` runs the `finally`.
            index = len(backend.calls)
            backend.calls.append(kwargs)
            try:
                if backend.raise_on_next is not None:
                    raise backend.raise_on_next
                if backend.block is not None:
                    # Endless stream that waits to be released before each packet.
                    while True:
                        backend.reached.set()
                        backend.block.wait(timeout=5)
                        yield np.asarray([0.1], dtype=np.float32), backend.rate
                for chunk in backend.chunks:
                    yield chunk, backend.rate
            finally:
                backend.closed_streams.append(index)
                if backend.raise_on_close is not None:
                    raise backend.raise_on_close

    return types.SimpleNamespace(QwenTTS=FakeQwenTTS, QwenTTSError=FakeQwenTTSError)


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()
    monkeypatch.setitem(sys.modules, "qwentts_cpp", _fake_qwentts_cpp(backend))
    return backend


def _run(coro):
    return asyncio.run(coro)


_VOICE_DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"


# --- pre-load validation (no fake needed: runs before the lazy import) -------


@pytest.mark.parametrize("field", ["model", "quant", "voice", "instruct", "language"])
@pytest.mark.parametrize("bad", ["", 3])
def test_invalid_string_fields_raise(field, bad):
    with pytest.raises(ConfigError, match=field):
        Qwen3Module({"type": "qwen3", field: bad})


@pytest.mark.parametrize("bad", [0, -0.5, True, "0.9", float("nan"), float("inf")])
def test_invalid_temperature_raises(bad):
    with pytest.raises(ConfigError, match="temperature"):
        Qwen3Module({"type": "qwen3", "temperature": bad})


def test_base_model_is_rejected_before_loading(fake):
    with pytest.raises(ConfigError, match="voice cloning"):
        Qwen3Module({"type": "qwen3", "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base"})
    assert fake.load_args is None


def test_voice_design_requires_instruct(fake):
    with pytest.raises(ConfigError, match="instruct"):
        Qwen3Module({"type": "qwen3", "model": _VOICE_DESIGN})
    assert fake.load_args is None


def test_voice_design_rejects_a_voice(fake):
    with pytest.raises(ConfigError, match="voice"):
        Qwen3Module(
            {
                "type": "qwen3",
                "model": _VOICE_DESIGN,
                "instruct": "Calm.",
                "voice": "ryan",
            }
        )
    assert fake.load_args is None


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "qwentts_cpp", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[qwen3\]"):
        Qwen3Module({"type": "qwen3"})


# --- construction ------------------------------------------------------------


def test_defaults(fake):
    module = Qwen3Module({"type": "qwen3"})
    assert fake.load_args == ("Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",)
    assert fake.load_kwargs == {"quant": "Q8_0", "log_level": "warning"}
    assert module.sample_rate == 24000

    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.calls == [{"text": "Hi.", "lang": "auto", "speaker": "aiden"}]


def test_set_fields_are_forwarded(fake):
    module = Qwen3Module(
        {
            "type": "qwen3",
            "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            "quant": "BF16",
            "voice": "Ryan",
            "instruct": "Speak cheerfully.",
            "language": "English",
            "temperature": 1,
        }
    )
    assert fake.load_args == ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",)
    assert fake.load_kwargs == {"quant": "BF16", "log_level": "warning"}

    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    # Matched case-insensitively, sent in the model's own spelling.
    assert fake.calls == [
        {
            "text": "Hi.",
            "lang": "english",
            "speaker": "ryan",
            "instruct": "Speak cheerfully.",
            "temperature": 1.0,
        }
    ]


def test_voice_design_sends_instruct_and_no_speaker(fake):
    fake.model_type = "voice_design"
    fake.speakers = []
    module = Qwen3Module(
        {"type": "qwen3", "model": _VOICE_DESIGN, "instruct": "A calm narrator."}
    )
    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.calls == [
        {"text": "Hi.", "lang": "auto", "instruct": "A calm narrator."}
    ]


def test_unknown_voice_is_config_error_and_closes(fake):
    with pytest.raises(ConfigError, match="voice.*'nobody'"):
        Qwen3Module({"type": "qwen3", "voice": "nobody"})
    assert fake.tts_closed


def test_unknown_language_is_config_error_and_closes(fake):
    with pytest.raises(ConfigError, match="language.*'klingon'"):
        Qwen3Module({"type": "qwen3", "language": "klingon"})
    assert fake.tts_closed


def test_library_rejection_is_config_error(fake):
    fake.raise_on_load = ValueError("Unsupported qwentts quant 'Q5'")
    with pytest.raises(ConfigError, match="Unsupported qwentts quant"):
        Qwen3Module({"type": "qwen3", "quant": "Q5"})


def test_other_load_failure_is_tts_error(fake):
    fake.raise_on_load = RuntimeError("Could not find qwentts.cpp shared library")
    with pytest.raises(TTSError, match="model load failed"):
        Qwen3Module({"type": "qwen3"})


# --- synthesize_text ---------------------------------------------------------


def test_synthesize_text_produces_clipped_int16_pcm(fake):
    module = Qwen3Module({"type": "qwen3"})
    received = bytearray()
    _run(module.synthesize_text("Hello.", TTSOptions(), received.extend))

    samples = np.frombuffer(bytes(received), dtype="<i2")
    # [0.0, 0.5, -0.5, 1.5→1.0, -2.0→-1.0] * 32767, truncated toward zero
    assert samples.tolist() == [0, 16383, -16383, 32767, -32767]
    assert fake.closed_streams == [0]


def test_synthesize_text_runs_one_stream_per_sentence(fake):
    module = Qwen3Module({"type": "qwen3"})
    received = bytearray()
    _run(
        module.synthesize_text(
            "Hello there. How are you?", TTSOptions(), received.extend
        )
    )

    assert [call["text"] for call in fake.calls] == ["Hello there.", "How are you?"]
    assert len(received) == 2 * 2 * 5  # five int16 samples per fake stream
    assert fake.closed_streams == [0, 1]


def test_unexpected_sample_rate_is_tts_error(fake):
    module = Qwen3Module({"type": "qwen3"})
    fake.rate = 16000
    with pytest.raises(TTSError, match="16000 Hz"):
        _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.closed_streams == [0]


def test_iteration_error_is_tts_error_and_closes(fake):
    module = Qwen3Module({"type": "qwen3"})
    fake.raise_on_next = RuntimeError("qt_synthesize failed with status -3")
    with pytest.raises(TTSError, match="status -3"):
        _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.closed_streams == [0]


def test_callback_error_propagates_unchanged_and_closes(fake):
    module = Qwen3Module({"type": "qwen3"})

    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _run(module.synthesize_text("Hi.", TTSOptions(), bad_callback))
    assert fake.closed_streams == [0]


def test_callback_error_wins_over_a_failing_close(fake):
    module = Qwen3Module({"type": "qwen3"})
    fake.raise_on_close = RuntimeError("native teardown failed")

    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _run(module.synthesize_text("Hi.", TTSOptions(), bad_callback))
    assert fake.closed_streams == [0]


async def test_cancel_closes_the_stream_before_cancelled_error(fake):
    module = Qwen3Module({"type": "qwen3"})
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi. Then more.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending packet return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed_streams == [0]
    assert len(fake.calls) == 1  # the next sentence never starts


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_synthesizes_per_sentence(fake):
    module = Qwen3Module({"type": "qwen3"})

    async def source():
        for piece in ["Hello wor", "ld. How are", " you? Fine"]:
            yield piece

    received = bytearray()
    _run(module.synthesize_text_stream(source(), TTSOptions(), received.extend))

    assert [call["text"] for call in fake.calls] == [
        "Hello world.",
        "How are you?",
        "Fine",
    ]
    assert len(received) == 3 * 2 * 5
    assert fake.closed_streams == [0, 1, 2]


async def test_text_stream_feeds_first_sentence_before_the_source_finishes(fake):
    module = Qwen3Module({"type": "qwen3"})
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(got_audio.set)

    async def source():
        yield "Hello world. "
        await got_audio.wait()
        yield "Second one."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    assert [call["text"] for call in fake.calls] == ["Hello world.", "Second one."]


async def test_text_stream_cancel_closes_the_stream_before_cancelled_error(fake):
    module = Qwen3Module({"type": "qwen3"})
    fake.block = threading.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1

    async def source():
        yield "Hi. "  # a complete sentence: its stream opens
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending packet return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
    assert fake.closed_streams == [0]
    assert [call["text"] for call in fake.calls] == ["Hi."]
