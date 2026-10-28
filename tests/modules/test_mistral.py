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
            _delta([-0.5, 0.25, -1.0]),
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


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_MISTRAL_KEY", "from-env")
    MistralModule({"type": "mistral", "api_key_env": "MY_MISTRAL_KEY", "voice_id": "v"})
    assert fake.client_kwargs == {"api_key": "from-env"}


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"voice_id": None}, "voice_id"),
        ({"voice_id": ""}, "voice_id"),
        ({"model": ""}, "model"),
        ({"expressiveness": 0.5}, "expressiveness"),
        ({"server_url": ""}, "server_url"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        MistralModule({**_BASE, **override})


def test_server_url_reaches_the_client_only_when_set(fake):
    MistralModule(_BASE)
    assert fake.client_kwargs == {"api_key": "k"}
    MistralModule({**_BASE, "server_url": "https://eu.example"})
    assert fake.client_kwargs == {"api_key": "k", "server_url": "https://eu.example"}


def test_defaults_reach_the_request(fake):
    module = MistralModule(_BASE)
    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert module.sample_rate == 24000
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
    module = MistralModule(
        {**_BASE, "model": "voxtral-x", "expressiveness": 2, "timeout": 2.5}
    )
    _run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fake.requests[0]["model"] == "voxtral-x"
    assert fake.requests[0]["expressiveness"] == 2.0
    assert fake.requests[0]["timeout_ms"] == 2500


# --- synthesize_text ---------------------------------------------------------


def test_deltas_become_int16_until_done(fake):
    received = bytearray()
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), received.extend))
    # The float32 deltas are converted to int16 (the scaling itself is
    # float_to_int16_pcm's); the unknown event is skipped and the delta after
    # `done` is never read.
    assert _samples(bytes(received)) == [0, 16383, -16383, 8191, -32767]
    assert fake.streams[0].closed


def test_split_sample_is_carried_over_and_audio_fed_in_100ms_slices(fake):
    # 125 ms at 24 kHz, the first delta ending mid-sample past one 100 ms slice:
    # one feeder spans the utterance, slicing at the declared rate.
    raw = np.tile(np.asarray([0.5, -0.5, 0.25], dtype="<f4"), 1000).tobytes()
    split = 4 * 2500 + 2
    fake.events = [_delta_bytes(raw[:split]), _delta_bytes(raw[split:]), _DONE]
    chunks: list[bytes] = []
    _run(MistralModule(_BASE).synthesize_text("Hi.", TTSOptions(), chunks.append))
    assert [len(c) for c in chunks] == [4800, 200, 1000]  # 2400 + 100, then 500
    assert _samples(b"".join(chunks)) == [16383, -16383, 8191] * 1000


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
        await asyncio.wait_for(task, timeout=5)
    assert fake.streams[0].closed


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_requests_per_sentence_as_the_source_arrives(fake):
    # Split pieces are grouped into sentences, and the first is spoken before
    # the source finishes: it is gated on that sentence's audio.
    module = MistralModule(_BASE)
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


def test_close_closes_the_sdk_client(fake):
    MistralModule(_BASE).close()
    assert fake.client_closed


# --- voice listing -----------------------------------------------------------


def _mistral_voice(voice_id, **fields):
    return types.SimpleNamespace(id=voice_id, **fields)


def test_list_voices_follows_every_page_then_closes_the_client(monkeypatch):
    pages = {
        None: (
            [_mistral_voice("a", name="James", gender="male", languages=["en_us"])],
            "t2",
        ),
        "t2": (
            [
                _mistral_voice(
                    "b",
                    name="Paul - Happy",
                    gender="male",
                    age=30,
                    languages=["en_us"],
                    tags=["sunny", "happy"],
                    description=None,
                )
            ],
            None,
        ),
    }
    calls: dict = {"searches": [], "closed": False}

    def search(**kwargs):
        calls["searches"].append(kwargs)
        data, token = pages[kwargs.get("page_token")]
        return types.SimpleNamespace(
            result=types.SimpleNamespace(data=data, next_page_token=token)
        )

    class Mistral:
        def __init__(self, **kwargs) -> None:
            calls["client"] = kwargs
            self.audio = types.SimpleNamespace(
                voices=types.SimpleNamespace(search=search)
            )

        def __exit__(self, *_exc) -> None:
            calls["closed"] = True

    package = types.ModuleType("mistralai")
    client = types.ModuleType("mistralai.client")
    client.Mistral = Mistral  # type: ignore[attr-defined]
    package.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mistralai", package)
    monkeypatch.setitem(sys.modules, "mistralai.client", client)

    james, paul = MistralModule.list_voices(
        {"type": "mistral", "api_key": "k", "server_url": "https://x", "timeout": 1.5}
    )

    assert calls["client"] == {"api_key": "k", "server_url": "https://x"}
    assert calls["searches"] == [
        {"page_size": 100, "timeout_ms": 1500},
        {"page_size": 100, "timeout_ms": 1500, "page_token": "t2"},
    ]
    assert calls["closed"]
    assert (james.config, james.gender, james.age, james.languages) == (
        {"voice_id": "a"},
        "male",
        None,
        ("en_us",),
    )
    assert (paul.name, paul.age, paul.tags) == (
        "Paul - Happy",
        "30",
        ("sunny", "happy"),
    )
