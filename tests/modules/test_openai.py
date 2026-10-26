"""Unit tests for OpenAIModule with the openai SDK faked.

The fast tier never calls the API, so `openai` is faked via `sys.modules`:
`OpenAI(...).audio.speech.with_streaming_response.create` records its kwargs and
returns a scripted streaming response.
"""

import asyncio
import sys
import threading
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.openai import OpenAIModule

_BASE = {"type": "openai", "api_key": "k", "voice": "v"}


def _pcm(samples) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


class _FakeResponse:
    """Stands in for the SDK's streaming response context manager: entering it
    sends the request, `iter_bytes()` yields the body's chunks."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self.closed = False

    def __enter__(self):
        if self._backend.raise_on_enter is not None:
            raise self._backend.raise_on_enter
        return self

    def __exit__(self, *_exc) -> bool:
        self.closed = True
        return False

    def iter_bytes(self):
        backend = self._backend
        if backend.block is not None:
            # Endless body that waits to be released after each chunk.
            while True:
                backend.reached.set()
                backend.block.wait(timeout=5)
                yield _pcm([1])
        for chunk in backend.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.requests: list[dict] = []
        self.responses: list[_FakeResponse] = []
        self.chunks: list = [_pcm([1, 2]), _pcm([3, -4, 5])]
        self.raise_on_enter: Exception | None = None
        self.block: threading.Event | None = None
        self.reached = threading.Event()

    @property
    def texts(self) -> list[str]:
        return [request["input"] for request in self.requests]


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class StreamingSpeech:
        def create(self, **kwargs):
            backend.requests.append(kwargs)
            response = _FakeResponse(backend)
            backend.responses.append(response)
            return response

    class OpenAI:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            speech = types.SimpleNamespace(with_streaming_response=StreamingSpeech())
            self.audio = types.SimpleNamespace(speech=speech)

    package = types.ModuleType("openai")
    package.OpenAI = OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", package)
    return backend


def _run(coro):
    return asyncio.run(coro)


def _say(module: OpenAIModule, callback=lambda _b: None, text: str = "Hi.") -> None:
    _run(module.synthesize_text(text, TTSOptions(), callback))


def _samples(pcm: bytes) -> list[int]:
    return np.frombuffer(pcm, dtype="<i2").tolist()


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[openai\]"):
        OpenAIModule(_BASE)


# --- config ------------------------------------------------------------------


def test_missing_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        OpenAIModule({"type": "openai", "voice": "v"})


def test_empty_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        OpenAIModule({"type": "openai", "api_key": "", "voice": "v"})


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_OPENAI_KEY", "from-env")
    OpenAIModule({"type": "openai", "api_key_env": "MY_OPENAI_KEY", "voice": "v"})
    assert fake.client_kwargs == {"api_key": "from-env"}


def test_literal_api_key_takes_precedence_over_env(fake, monkeypatch):
    monkeypatch.setenv("MY_OPENAI_KEY", "from-env")
    OpenAIModule({**_BASE, "api_key_env": "MY_OPENAI_KEY"})
    assert fake.client_kwargs == {"api_key": "k"}


def test_api_key_env_pointing_to_unset_var_raises(fake, monkeypatch):
    monkeypatch.delenv("MY_OPENAI_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_OPENAI_KEY"):
        OpenAIModule({"type": "openai", "api_key_env": "MY_OPENAI_KEY", "voice": "v"})


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"api_key": 3}, "api_key"),
        ({"api_key": None, "api_key_env": 3}, "api_key_env"),
        ({"voice": None}, "voice"),
        ({"voice": ""}, "voice"),
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"instructions": ""}, "instructions"),
        ({"instructions": 3}, "instructions"),
        ({"speed": 0.2}, "speed"),
        ({"speed": 4.5}, "speed"),
        ({"speed": True}, "speed"),
        ({"speed": "1"}, "speed"),
        ({"speed": float("nan")}, "speed"),
        ({"base_url": ""}, "base_url"),
        ({"base_url": 3}, "base_url"),
        ({"sample_rate": 0, "base_url": "http://x/v1"}, "sample_rate"),
        ({"sample_rate": 24000.0}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"sample_rate": 44100}, "base_url"),  # OpenAI itself is fixed at 24 kHz
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        OpenAIModule({**_BASE, **override})


def test_base_url_reaches_the_client_only_when_set(fake):
    OpenAIModule(_BASE)
    assert fake.client_kwargs == {"api_key": "k"}
    OpenAIModule({**_BASE, "base_url": "http://localhost:8000/v1"})
    assert fake.client_kwargs == {
        "api_key": "k",
        "base_url": "http://localhost:8000/v1",
    }


def test_sample_rate_defaults_to_24k(fake):
    assert OpenAIModule(_BASE).sample_rate == 24000


def test_sample_rate_declares_a_compatible_servers_rate(fake):
    module = OpenAIModule({**_BASE, "base_url": "http://x/v1", "sample_rate": 44100})
    assert module.sample_rate == 44100


def test_defaults_reach_the_request(fake):
    _say(OpenAIModule(_BASE))
    assert fake.requests == [
        {
            "input": "Hi.",
            "model": "gpt-4o-mini-tts",
            "voice": "v",
            "response_format": "pcm",
        }
    ]


def test_custom_values_reach_the_request(fake):
    module = OpenAIModule(
        {**_BASE, "model": "tts-1", "instructions": "Whisper.", "speed": 2}
    )
    _say(module)
    request = fake.requests[0]
    assert request["model"] == "tts-1"
    assert request["instructions"] == "Whisper."
    assert request["speed"] == 2.0


# --- synthesize_text ---------------------------------------------------------


def test_chunks_reach_the_callback_unchanged(fake):
    received = bytearray()
    _say(OpenAIModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [1, 2, 3, -4, 5]
    assert fake.responses[0].closed


def test_odd_byte_is_carried_over_and_trailing_half_sample_dropped(fake):
    raw = _pcm([100, -200, 300])
    fake.chunks = [raw[:5], raw[5:], b"\x07"]
    chunks: list[bytes] = []
    _say(OpenAIModule(_BASE), chunks.append)
    assert all(len(c) % 2 == 0 for c in chunks)
    assert _samples(b"".join(chunks)) == [100, -200, 300]


def test_body_shorter_than_the_wav_check_is_fed(fake):
    fake.chunks = [_pcm([9])]
    received = bytearray()
    _say(OpenAIModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [9]


def test_large_chunk_is_fed_in_100ms_slices(fake):
    fake.chunks = [_pcm(np.ones(48000))]  # 2 s at 24 kHz
    chunks: list[bytes] = []
    _say(OpenAIModule(_BASE), chunks.append)
    assert [len(c) for c in chunks] == [4800] * 20  # 2400 int16 samples each


def test_slices_follow_the_declared_sample_rate(fake):
    fake.chunks = [_pcm(np.ones(44100))]  # 1 s at 44.1 kHz
    module = OpenAIModule({**_BASE, "base_url": "http://x/v1", "sample_rate": 44100})
    chunks: list[bytes] = []
    _say(module, chunks.append)
    assert [len(c) for c in chunks] == [8820] * 10


def test_blank_text_makes_no_request(fake):
    received = bytearray()
    _say(OpenAIModule(_BASE), received.extend, text="  \n")
    assert fake.requests == []
    assert received == b""


def test_wav_body_is_tts_error_before_any_feed(fake):
    fake.chunks = [b"RI", b"FF\x24\x00\x00\x00WAVEfmt ", _pcm([1, 2, 3])]
    received = bytearray()
    with pytest.raises(TTSError, match="returned WAV"):
        _say(OpenAIModule(_BASE), received.extend)
    assert received == b""
    assert fake.responses[0].closed


def test_request_error_is_tts_error(fake):
    fake.raise_on_enter = RuntimeError("Error code: 404 - model_not_found")
    with pytest.raises(TTSError, match="OpenAI request failed: Error code: 404"):
        _say(OpenAIModule(_BASE))


def test_mid_body_error_is_tts_error_and_closes(fake):
    fake.chunks = [_pcm([1]), ConnectionError("connection reset")]
    with pytest.raises(TTSError, match="connection reset"):
        _say(OpenAIModule(_BASE))
    assert fake.responses[0].closed


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(OpenAIModule(_BASE), bad_callback)
    assert fake.responses[0].closed


async def test_cancel_closes_the_response_before_cancelled_error(fake):
    module = OpenAIModule(_BASE)
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the body, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending chunk return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.responses[0].closed


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_requests_per_sentence(fake):
    async def source():
        for piece in ["Hello wor", "ld. How are", " you? Fine"]:
            yield piece

    received = bytearray()
    _run(
        OpenAIModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), received.extend
        )
    )

    assert fake.texts == ["Hello world.", "How are you?", "Fine"]
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake response
    assert all(response.closed for response in fake.responses)


async def test_text_stream_feeds_first_sentence_before_the_source_finishes(fake):
    module = OpenAIModule(_BASE)
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
    assert fake.texts == ["Hello world.", "Second one."]


async def test_text_stream_cancel_closes_the_response_before_cancelled_error(fake):
    module = OpenAIModule(_BASE)
    fake.block = threading.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1

    async def source():
        yield "Hi. "  # a complete sentence: its request opens
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
    assert fake.responses[0].closed
    assert fake.texts == ["Hi."]
