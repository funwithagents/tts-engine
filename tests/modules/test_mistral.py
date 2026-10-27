"""Unit tests for MistralModule with the mistralai SDK faked.

The fast tier never calls the API, so `mistralai` (and `mistralai.client`, the
3.x import path) is faked via `sys.modules`: `Mistral(...).audio.speech.complete`
records its kwargs and returns a scripted event stream.
"""

import asyncio
import base64
import sys
import threading
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.mistral import MistralModule

_BASE = {"type": "mistral", "api_key": "k", "voice_id": "v"}


def _delta(samples) -> types.SimpleNamespace:
    raw = np.asarray(samples, dtype="<f4").tobytes()
    return _delta_bytes(raw)


def _delta_bytes(raw: bytes) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        event="speech.audio.delta",
        data=types.SimpleNamespace(audio_data=base64.b64encode(raw).decode()),
    )


_DONE = types.SimpleNamespace(event="speech.audio.done", data=types.SimpleNamespace())


class _FakeEvents:
    """Stands in for the SDK's `EventStream`: a context manager iterating events."""

    def __init__(self, backend: "_FakeBackend") -> None:
        self._backend = backend
        self._events = iter(backend.events)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        self.closed = True
        if self._backend.raise_on_close is not None:
            raise self._backend.raise_on_close
        return False

    def __iter__(self):
        return self

    def __next__(self):
        backend = self._backend
        if backend.raise_on_next is not None:
            raise backend.raise_on_next
        if backend.block is not None:
            # Endless stream that waits to be released after each event.
            backend.reached.set()
            backend.block.wait(timeout=5)
            return _delta([0.1])
        return next(self._events)


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.client_closed = False
        self.requests: list[dict] = []
        self.streams: list[_FakeEvents] = []
        self.events: list = [
            _delta([0.0, 0.5]),
            types.SimpleNamespace(event="speech.other", data=None),  # skipped
            _delta([-0.5, 1.5, -2.0]),  # out of range: clipped
            _DONE,
            _delta([0.25]),  # after done: never read
        ]
        self.raise_on_request: Exception | None = None
        self.raise_on_next: Exception | None = None
        self.raise_on_close: Exception | None = None
        self.block: threading.Event | None = None
        self.reached = threading.Event()

    @property
    def texts(self) -> list[str]:
        return [request["input"] for request in self.requests]


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class Speech:
        def complete(self, **kwargs):
            backend.requests.append(kwargs)
            if backend.raise_on_request is not None:
                raise backend.raise_on_request
            events = _FakeEvents(backend)
            backend.streams.append(events)
            return events

    class Mistral:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.audio = types.SimpleNamespace(speech=Speech())

        def __exit__(self, *_exc) -> None:
            # The speakeasy SDK closes its HTTP client here; there is no close().
            backend.client_closed = True

    package = types.ModuleType("mistralai")
    client = types.ModuleType("mistralai.client")
    client.Mistral = Mistral  # type: ignore[attr-defined]
    package.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mistralai", package)
    monkeypatch.setitem(sys.modules, "mistralai.client", client)
    return backend


def _run(coro):
    return asyncio.run(coro)


def _samples(pcm: bytes) -> list[int]:
    return np.frombuffer(pcm, dtype="<i2").tolist()


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "mistralai", None)  # -> ImportError
    monkeypatch.setitem(sys.modules, "mistralai.client", None)
    with pytest.raises(ConfigError, match=r"tts-engine\[mistral\]"):
        MistralModule(_BASE)


# --- config ------------------------------------------------------------------


def test_missing_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        MistralModule({"type": "mistral", "voice_id": "v"})


def test_empty_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        MistralModule({"type": "mistral", "api_key": "", "voice_id": "v"})


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_MISTRAL_KEY", "from-env")
    MistralModule({"type": "mistral", "api_key_env": "MY_MISTRAL_KEY", "voice_id": "v"})
    assert fake.client_kwargs == {"api_key": "from-env"}


def test_literal_api_key_takes_precedence_over_env(fake, monkeypatch):
    monkeypatch.setenv("MY_MISTRAL_KEY", "from-env")
    MistralModule({**_BASE, "api_key_env": "MY_MISTRAL_KEY"})
    assert fake.client_kwargs == {"api_key": "k"}


def test_api_key_env_pointing_to_unset_var_raises(fake, monkeypatch):
    monkeypatch.delenv("MY_MISTRAL_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_MISTRAL_KEY"):
        MistralModule(
            {"type": "mistral", "api_key_env": "MY_MISTRAL_KEY", "voice_id": "v"}
        )


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"api_key": 3}, "api_key"),
        ({"api_key": None, "api_key_env": 3}, "api_key_env"),
        ({"voice_id": None}, "voice_id"),
        ({"voice_id": ""}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"expressiveness": 0.5}, "expressiveness"),
        ({"expressiveness": True}, "expressiveness"),
        ({"expressiveness": "2"}, "expressiveness"),
        ({"expressiveness": float("inf")}, "expressiveness"),
        ({"server_url": ""}, "server_url"),
        ({"server_url": 3}, "server_url"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": "30"}, "timeout"),
        ({"timeout": float("inf")}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        MistralModule({**_BASE, **override})


def test_timeout_reaches_the_request_in_milliseconds(fake):
    module = MistralModule({**_BASE, "timeout": 2.5})
    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.requests[0]["timeout_ms"] == 2500


def test_server_url_reaches_the_client_only_when_set(fake):
    MistralModule(_BASE)
    assert fake.client_kwargs == {"api_key": "k"}
    MistralModule({**_BASE, "server_url": "https://eu.example"})
    assert fake.client_kwargs == {"api_key": "k", "server_url": "https://eu.example"}


def test_sample_rate_is_24k(fake):
    assert MistralModule(_BASE).sample_rate == 24000


def test_defaults_reach_the_request(fake):
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.requests == [
        {
            "input": "Hi.",
            "model": "voxtral-mini-tts-3-0",
            "voice_id": "v",
            "response_format": "pcm",
            "stream": True,
            "timeout_ms": 30000,
        }
    ]


def test_custom_values_reach_the_request(fake):
    module = MistralModule({**_BASE, "model": "voxtral-x", "expressiveness": 2})
    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.requests[0]["model"] == "voxtral-x"
    assert fake.requests[0]["expressiveness"] == 2.0


# --- synthesize_text ---------------------------------------------------------


def test_deltas_become_clipped_int16_until_done(fake):
    received = bytearray()
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), received.extend))
    # [0.0, 0.5, -0.5, 1.5→1.0, -2.0→-1.0] * 32767, truncated toward zero; the
    # unknown event is skipped and the delta after `done` is never read.
    assert _samples(bytes(received)) == [0, 16383, -16383, 32767, -32767]
    assert fake.streams[0].closed


def test_sample_split_across_deltas_is_carried_over(fake):
    raw = np.asarray([0.5, -0.5, 0.25], dtype="<f4").tobytes()
    fake.events = [
        _delta_bytes(raw[:5]),
        _delta_bytes(raw[5:9]),
        _delta_bytes(raw[9:]),
        _DONE,
    ]
    received = bytearray()
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), received.extend))
    assert _samples(bytes(received)) == [16383, -16383, 8191]


def test_large_delta_is_fed_in_100ms_slices(fake):
    fake.events = [_delta(np.zeros(48000)), _DONE]  # 2 s at 24 kHz
    chunks: list[bytes] = []
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), chunks.append))
    assert [len(c) for c in chunks] == [4800] * 20  # 2400 int16 samples each


def test_request_error_is_tts_error(fake):
    fake.raise_on_request = RuntimeError("Voice 'nope' not found.")
    with pytest.raises(TTSError, match="Mistral request failed: Voice 'nope'"):
        _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), lambda _b: None))


def test_mid_stream_error_is_tts_error_and_closes(fake):
    fake.raise_on_next = ConnectionError("connection reset")
    with pytest.raises(TTSError, match="connection reset"):
        _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.streams[0].closed


def test_undecodable_audio_is_tts_error(fake):
    fake.events = [
        types.SimpleNamespace(
            event="speech.audio.delta",
            data=types.SimpleNamespace(audio_data="not base64!"),
        )
    ]
    with pytest.raises(TTSError, match="Mistral request failed"):
        _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), lambda _b: None))


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), bad_callback))
    assert fake.streams[0].closed


def test_callback_error_wins_over_a_failing_close(fake):
    fake.raise_on_close = RuntimeError("connection teardown failed")

    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), bad_callback))
    assert fake.streams[0].closed


async def test_cancel_closes_the_stream_before_cancelled_error(fake):
    module = MistralModule(_BASE)
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending event return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.streams[0].closed


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_requests_per_sentence(fake):
    async def source():
        for piece in ["Hello wor", "ld. How are", " you? Fine"]:
            yield piece

    received = bytearray()
    _run(
        MistralModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), received.extend
        )
    )

    assert fake.texts == ["Hello world.", "How are you?", "Fine"]
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake stream
    assert all(stream.closed for stream in fake.streams)


async def test_text_stream_feeds_first_sentence_before_the_source_finishes(fake):
    module = MistralModule(_BASE)
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


async def test_text_stream_cancel_closes_the_stream_before_cancelled_error(fake):
    module = MistralModule(_BASE)
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
    assert fake.streams[0].closed
    assert fake.texts == ["Hi."]


def test_close_closes_the_sdk_client(fake):
    MistralModule(_BASE).close()
    assert fake.client_closed
