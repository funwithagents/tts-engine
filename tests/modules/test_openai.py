"""Unit tests for OpenAIModule (Realtime API) with the openai SDK faked.

The fast tier never opens a socket, so `openai` and `websockets.sync.client`
are faked via `sys.modules`: `OpenAI(...).realtime.connect(model=...)` returns
a context manager around a scripted connection whose `recv()` blocks on a
queue that each `response.create` fills with the scripted server events.
"""

import asyncio
import base64
import queue
import sys
import threading
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.openai import OpenAIModule
from tts_engine.modules.openai.module import _VERBATIM

_BASE = {"type": "openai", "api_key": "k"}
_NS = types.SimpleNamespace


def _pcm(samples) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


def _delta(pcm: bytes) -> _NS:
    return _NS(type="response.output_audio.delta", delta=base64.b64encode(pcm).decode())


def _done(status: str = "completed") -> _NS:
    return _NS(type="response.done", response=_NS(status=status, status_details=None))


class _Closed(Exception):
    """What the fake `recv()` raises once the socket is closed."""


class _Connection:
    def __init__(self, server: "_Server") -> None:
        self._server = server
        self.inbox: queue.Queue = queue.Queue()
        self.session = _NS(update=lambda *, session: server.sessions.append(session))
        self.response = _NS(create=self._create, cancel=self._cancel)
        self.inbox.put(_NS(type="session.created"))

    def _create(self, *, response: dict) -> None:
        server = self._server
        server.responses.append(response)
        number = len(server.responses)
        events = server.script(number)
        if events is None:  # the default: one completed response
            events = [_delta(chunk) for chunk in server.chunks] + [_done()]
        self.inbox.put(_NS(type="response.created", response=_NS(id=f"resp_{number}")))
        for event in events:
            self.inbox.put(event)

    def _cancel(self, *, response_id: str) -> None:
        self._server.cancels.append(response_id)

    def recv(self):
        item = self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.inbox.put(_Closed("closed"))
        self._server.closed.set()


class _Server:
    """Shared state behind the fake client: records calls, scripts events."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.client_closed = False
        self.models: list[str] = []
        self.sessions: list[dict] = []
        self.responses: list[dict] = []
        self.cancels: list[str] = []
        self.chunks: list[bytes] = [_pcm([1, 2]), _pcm([3, -4, 5])]
        # response number -> events after `response.created`, None = default
        self.script = lambda _number: None
        self.raise_on_enter: Exception | None = None
        self.closed = threading.Event()

    @property
    def texts(self) -> list[str]:
        return [r["input"][0]["content"][0]["text"] for r in self.responses]


@pytest.fixture
def fake(monkeypatch) -> _Server:
    server = _Server()

    class Manager:
        def __enter__(self):
            if server.raise_on_enter is not None:
                raise server.raise_on_enter
            self.connection = _Connection(server)
            return self.connection

        def __exit__(self, *_exc) -> None:
            self.connection.close()

    def connect(*, model: str) -> Manager:
        server.models.append(model)
        return Manager()

    class OpenAI:
        def __init__(self, **kwargs) -> None:
            server.client_kwargs = kwargs
            self.realtime = _NS(connect=connect)

        def close(self) -> None:
            server.client_closed = True

    package = types.ModuleType("openai")
    package.OpenAI = OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", package)
    for name in ("websockets", "websockets.sync", "websockets.sync.client"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    return server


def _say(module: OpenAIModule, callback=lambda _b: None, text: str = "Hi.") -> None:
    asyncio.run(module.synthesize_text(text, TTSOptions(), callback))


# --- construction ------------------------------------------------------------


@pytest.mark.parametrize("missing", ["openai", "websockets.sync.client"])
def test_missing_extra_is_config_error(fake, monkeypatch, missing):
    monkeypatch.setitem(sys.modules, missing, None)
    with pytest.raises(ConfigError, match=r"tts-engine\[openai\]"):
        OpenAIModule(_BASE)


@pytest.mark.parametrize(
    "field, value",
    [
        ("voice", ""),
        ("model", ""),
        ("instructions", ""),
        ("speed", 0.2),
        ("speed", 2.0),
        ("speed", True),
        ("speed", float("nan")),
        ("timeout", 0),
        ("timeout", True),
    ],
)
def test_invalid_field_is_config_error(fake, field, value):
    with pytest.raises(ConfigError, match=field):
        OpenAIModule({**_BASE, field: value})


def test_defaults_reach_the_socket(fake):
    module = OpenAIModule(_BASE)
    _say(module)
    assert fake.client_kwargs == {"api_key": "k"}
    assert module.sample_rate == 24000
    assert fake.models == ["gpt-realtime-2.1-mini"]
    assert fake.sessions == [
        {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": _VERBATIM,
            "audio": {
                "input": {"turn_detection": None},
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": "marin",
                },
            },
        }
    ]
    assert fake.responses == [
        {
            "conversation": "none",
            "output_modalities": ["audio"],
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hi."}],
                }
            ],
        }
    ]


def test_options_reach_the_session(fake):
    config = {
        **_BASE,
        "model": "gpt-realtime-2.1",
        "voice": "cedar",
        "speed": 1.2,
        "instructions": "Calm and slow.",
    }
    _say(OpenAIModule(config))
    assert fake.models == ["gpt-realtime-2.1"]
    (session,) = fake.sessions
    assert session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["output"]["speed"] == 1.2
    # The voice direction is added after the verbatim rule, never instead of it.
    assert session["instructions"] == f"{_VERBATIM}\n\nVoice direction: Calm and slow."


# --- synthesize_text ---------------------------------------------------------


def test_audio_deltas_are_decoded_and_fed_in_order(fake):
    received = bytearray()
    _say(OpenAIModule(_BASE), received.extend)
    assert np.frombuffer(bytes(received), dtype="<i2").tolist() == [1, 2, 3, -4, 5]
    assert fake.closed.wait(1)


def test_a_long_delta_is_fed_in_100ms_slices(fake):
    fake.chunks = [_pcm(np.arange(24000) % 1000)]  # one second
    chunks: list[bytes] = []
    _say(OpenAIModule(_BASE), chunks.append)
    assert [len(c) for c in chunks] == [4800] * 10


def test_a_long_text_is_sliced_into_responses_over_one_socket(fake):
    text = "This sentence is about fifty characters long, ok. " * 80
    _say(OpenAIModule(_BASE), text=text)
    assert len(fake.models) == 1
    assert len(fake.responses) >= 3
    assert all(len(t) <= 1500 for t in fake.texts)
    assert "".join(fake.texts) == text


def test_blank_text_opens_no_socket(fake):
    _say(OpenAIModule(_BASE), text=" \n")
    assert fake.models == []


@pytest.mark.parametrize(
    "events, match",
    [
        (
            [
                _NS(
                    type="error",
                    error=_NS(code="invalid_value", type="x", message="bad voice"),
                )
            ],
            "invalid_value: bad voice",
        ),
        ([_done("failed")], "response failed"),
        ([_done("incomplete")], "response incomplete"),
        ([_delta(_pcm([1])), ConnectionError("connection reset")], "connection reset"),
        ([_NS(type="response.output_audio.delta", delta="not base64!")], "bad audio"),
    ],
    ids=["error-event", "failed", "incomplete", "reader-failure", "bad-delta"],
)
def test_server_failures_are_tts_error_and_close(fake, events, match):
    fake.script = lambda _n: events
    with pytest.raises(TTSError, match=match):
        _say(OpenAIModule(_BASE))
    assert fake.closed.wait(1)


def test_handshake_failure_is_tts_error(fake):
    fake.raise_on_enter = OSError("connection refused")
    with pytest.raises(TTSError, match="OpenAI Realtime request failed: connection"):
        _say(OpenAIModule(_BASE))


def test_a_silent_server_times_out(fake):
    fake.script = lambda _n: []  # `response.created`, then nothing
    with pytest.raises(TTSError, match="no answer within 0.1 s"):
        _say(OpenAIModule({**_BASE, "timeout": 0.1}))


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(OpenAIModule(_BASE), bad_callback)
    assert fake.closed.wait(1)


async def test_cancel_cancels_the_response_by_id_and_closes(fake):
    fake.script = lambda _n: [_delta(_pcm([1]))]  # audio, then the server stalls
    got_audio = threading.Event()
    task = asyncio.create_task(
        OpenAIModule(_BASE).synthesize_text(
            "Hi.", TTSOptions(), lambda _b: got_audio.set()
        )
    )
    await asyncio.to_thread(got_audio.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake.cancels == ["resp_1"]
    assert await asyncio.to_thread(fake.closed.wait, 1)


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_speaks_each_sentence_over_one_socket(fake):
    # The first sentence is spoken before the source finishes: the source's
    # last piece waits on that sentence's audio.
    module = OpenAIModule(_BASE)
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
    assert len(fake.models) == 1
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake response


async def test_text_stream_cancel_with_the_source_open(fake):
    got_audio = threading.Event()

    async def source():
        yield "First sentence. "
        await asyncio.Event().wait()  # never yields again

    task = asyncio.create_task(
        OpenAIModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), lambda _b: got_audio.set()
        )
    )
    await asyncio.to_thread(got_audio.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake.texts == ["First sentence."]
    assert await asyncio.to_thread(fake.closed.wait, 1)


def test_close_closes_the_sdk_client(fake):
    OpenAIModule(_BASE).close()
    assert fake.client_closed
