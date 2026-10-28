"""Unit tests for OpenAIModule (Realtime API) with the openai SDK faked.

The fast tier never opens a socket, so `openai` and `websockets.sync.client`
are faked via `sys.modules`: `OpenAI(...).realtime.connect(model=...)` returns
a context manager around a scripted connection whose `recv()` blocks on a
queue that `session.update` and each `response.create` fill with the scripted
server events. The module holds one socket from construction to `close()`.
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


def _created(response_id: str) -> _NS:
    return _NS(type="response.created", response=_NS(id=response_id))


def _delta(pcm: bytes, response_id: str = "resp_1") -> _NS:
    return _NS(
        type="response.output_audio.delta",
        response_id=response_id,
        delta=base64.b64encode(pcm).decode(),
    )


def _done(status: str = "completed", response_id: str = "resp_1") -> _NS:
    return _NS(
        type="response.done",
        response=_NS(id=response_id, status=status, status_details=None),
    )


def _error(code: str, event_id: str | None) -> _NS:
    return _NS(
        type="error",
        error=_NS(code=code, type="x", message=f"{code} message", event_id=event_id),
    )


class _Closed(Exception):
    """What the fake `recv()` raises once the socket is closed."""


class _Connection:
    def __init__(self, server: "_Server") -> None:
        self._server = server
        self.inbox: queue.Queue = queue.Queue()
        self.session = _NS(update=self._update)
        self.response = _NS(create=self._create, cancel=self._cancel)
        self.responses = 0
        self.closed = threading.Event()
        self.inbox.put(_NS(type="session.created"))

    def _update(self, *, session: dict, event_id: str) -> None:
        server = self._server
        server.sessions.append(session)
        server.event_ids.append(event_id)
        if server.reject_setup is not None:
            self.inbox.put(_error(server.reject_setup, event_id))
        elif not server.silent_setup:
            self.inbox.put(_NS(type="session.updated"))

    def _create(self, *, response: dict, event_id: str) -> None:
        server = self._server
        server.responses.append(response)
        server.event_ids.append(event_id)
        server.create_ids.append(event_id)
        self.responses += 1
        number = len(server.responses)
        response_id = f"resp_{number}"
        events = server.script(number)
        if events is None:  # the default: one completed response
            events = [_delta(chunk, response_id) for chunk in server.chunks]
            events.append(_done(response_id=response_id))
        if server.auto_created:
            self.inbox.put(_created(response_id))
        for event in events:
            self.inbox.put(event)

    def _cancel(self, *, response_id: str, event_id: str) -> None:
        self._server.cancels.append((response_id, event_id))
        self.inbox.put(_done("cancelled", response_id))

    def recv(self):
        item = self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.inbox.put(_Closed("closed"))
        self.closed.set()


class _Server:
    """Shared state behind the fake client: records calls, scripts events."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.client_closed = False
        self.models: list[str] = []
        self.connections: list[_Connection] = []
        self.sessions: list[dict] = []
        self.responses: list[dict] = []
        self.event_ids: list[str] = []
        self.create_ids: list[str] = []
        self.cancels: list[tuple[str, str]] = []
        self.chunks: list[bytes] = [_pcm([1, 2]), _pcm([3, -4, 5])]
        # response number -> events after `response.created`, None = default
        self.script = lambda _number: None
        self.auto_created = True
        self.raise_on_enter: Exception | None = None
        self.reject_setup: str | None = None
        self.silent_setup = False

    @property
    def connects(self) -> int:
        return len(self.models)

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
            server.connections.append(self.connection)
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


def _samples(received: bytearray) -> list[int]:
    return np.frombuffer(bytes(received), dtype="<i2").tolist()


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
    assert fake.connects == 0


def test_construction_connects_and_configures_the_session(fake):
    OpenAIModule(_BASE)
    assert fake.connects == 1
    assert len(fake.sessions) == 1
    assert fake.event_ids[0].startswith("tts-")
    assert fake.responses == []


def test_a_rejected_setup_is_config_error_leaving_no_socket(fake):
    fake.reject_setup = "invalid_api_key"
    with pytest.raises(ConfigError, match="OpenAI module: invalid_api_key"):
        OpenAIModule(_BASE)
    assert fake.connections[0].closed.wait(1)
    assert fake.client_closed


@pytest.mark.parametrize("failure", ["silent", "handshake"])
def test_a_failed_setup_is_tts_error(fake, failure):
    if failure == "silent":
        fake.silent_setup = True
    else:
        fake.raise_on_enter = OSError("connection refused")
    with pytest.raises(TTSError, match="OpenAI Realtime request failed"):
        OpenAIModule({**_BASE, "timeout": 0.1})
    assert fake.client_closed
    if failure == "silent":
        assert fake.connections[0].closed.wait(1)


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
    # Every client event carries its own id.
    assert len(set(fake.event_ids)) == 2


def test_options_reach_the_session(fake):
    config = {
        **_BASE,
        "model": "gpt-realtime-2.1",
        "voice": "cedar",
        "speed": 1.2,
        "instructions": "Calm and slow.",
    }
    OpenAIModule(config)
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
    assert _samples(received) == [1, 2, 3, -4, 5]
    assert not fake.connections[0].closed.is_set()


def test_a_long_delta_is_fed_in_100ms_slices(fake):
    fake.chunks = [_pcm(np.arange(24000) % 1000)]  # one second
    chunks: list[bytes] = []
    _say(OpenAIModule(_BASE), chunks.append)
    assert [len(c) for c in chunks] == [4800] * 10


def test_utterances_reuse_the_socket(fake):
    module = OpenAIModule(_BASE)
    _say(module)
    _say(module)
    assert fake.connects == 1
    assert fake.connections[0].responses == 2


def test_a_long_text_is_sliced_into_responses_over_one_socket(fake):
    text = "This sentence is about fifty characters long, ok. " * 80
    _say(OpenAIModule(_BASE), text=text)
    assert fake.connects == 1
    assert len(fake.responses) >= 3
    assert all(len(t) <= 1500 for t in fake.texts)
    assert "".join(fake.texts) == text


def test_blank_text_sends_nothing(fake):
    module = OpenAIModule(_BASE)
    module._socket.opened -= 56 * 60  # type: ignore[union-attr]  (no rotation either)
    _say(module, text=" \n")
    assert fake.connects == 1
    assert fake.responses == []


def test_events_of_other_responses_and_utterances_are_skipped(fake):
    fake.script = lambda _n: [
        _created("resp_old"),
        _delta(_pcm([9, 9]), "resp_old"),
        _error("response_cancel_not_active", "tts-0"),
        _done("cancelled", "resp_old"),
        _delta(_pcm([1, 2])),
        _done(),
    ]
    received = bytearray()
    _say(OpenAIModule(_BASE), received.extend)
    assert _samples(received) == [1, 2]


@pytest.mark.parametrize(
    "events, match",
    [
        (lambda f: [_error("invalid_value", f.create_ids[-1])], "invalid_value"),
        (lambda f: [_error("server_error", None)], "server_error"),
        (lambda f: [_done("failed")], "response failed"),
        (lambda f: [_done("incomplete")], "response incomplete"),
        (
            lambda f: [
                _NS(
                    type="response.output_audio.delta",
                    response_id="resp_1",
                    delta="no!",
                )
            ],
            "bad audio",
        ),
        (lambda f: [], "no answer within 0.1 s"),
    ],
    ids=["own-error", "server-error", "failed", "incomplete", "bad-delta", "silent"],
)
def test_server_failures_are_tts_error_and_keep_the_socket(fake, events, match):
    module = OpenAIModule({**_BASE, "timeout": 0.1})
    fake.script = lambda n: events(fake) if n == 1 else None
    with pytest.raises(TTSError, match=match):
        _say(module)
    assert not fake.connections[0].closed.is_set()
    _say(module)
    assert fake.connects == 1


def test_a_reader_failure_marks_the_socket_dead_and_the_next_call_reconnects(fake):
    module = OpenAIModule(_BASE)
    fake.script = lambda n: (
        [_delta(_pcm([1])), ConnectionError("connection reset")] if n == 1 else None
    )
    with pytest.raises(TTSError, match="connection reset"):
        _say(module)
    _say(module)
    assert fake.connects == 2
    assert fake.connections[0].closed.wait(1)
    assert fake.connections[1].responses == 1


def test_a_failed_reconnect_fails_that_utterance_only(fake):
    module = OpenAIModule(_BASE)
    fake.script = lambda n: [ConnectionError("reset")] if n == 1 else None
    with pytest.raises(TTSError, match="reset"):
        _say(module)
    fake.raise_on_enter = OSError("connection refused")
    with pytest.raises(TTSError, match="connection refused"):
        _say(module)
    assert fake.connects == 2
    fake.raise_on_enter = None
    _say(module)
    assert fake.connects == 3


def test_a_rejected_reconnect_is_tts_error(fake):
    module = OpenAIModule(_BASE)
    module._socket.dead.set()  # type: ignore[union-attr]
    fake.reject_setup = "invalid_api_key"
    with pytest.raises(TTSError, match="invalid_api_key"):
        _say(module)


def test_an_old_socket_is_rotated_before_the_utterance(fake):
    module = OpenAIModule(_BASE)
    module._socket.opened -= 56 * 60  # type: ignore[union-attr]
    _say(module, text="One. " * 400)  # several responses, all on the new socket
    assert fake.connects == 2
    assert fake.connections[0].closed.wait(1)
    assert fake.connections[0].responses == 0
    assert fake.connections[1].responses == len(fake.responses) > 1


def test_callback_error_propagates_unchanged_and_keeps_the_socket(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    module = OpenAIModule(_BASE)
    with pytest.raises(ValueError, match="playback failed"):
        _say(module, bad_callback)
    assert [rid for rid, _ in fake.cancels] == ["resp_1"]
    # The cancelled response's tail is skipped by the next utterance.
    received = bytearray()
    _say(module, received.extend)
    assert fake.connects == 1
    assert _samples(received) == [1, 2, 3, -4, 5]


async def test_cancel_cancels_the_response_by_id_and_keeps_the_socket(fake):
    module = OpenAIModule(_BASE)
    fake.script = lambda _n: [_delta(_pcm([1]))]  # audio, then the server stalls
    got_audio = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: got_audio.set())
    )
    await asyncio.to_thread(got_audio.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    ((response_id, event_id),) = fake.cancels
    assert response_id == "resp_1"
    assert event_id.startswith("tts-") and event_id not in fake.create_ids
    assert not fake.connections[0].closed.is_set()


async def test_a_cancel_before_response_created_waits_for_it(fake):
    module = OpenAIModule(_BASE)
    fake.auto_created = False
    fake.script = lambda _n: []
    task = asyncio.create_task(module.synthesize_text("Hi.", TTSOptions(), print))
    while not fake.responses:
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.1)  # the worker sees `stop` with no response id yet
    assert fake.cancels == []
    fake.connections[0].inbox.put(_created("resp_1"))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert [rid for rid, _ in fake.cancels] == ["resp_1"]
    assert not fake.connections[0].closed.is_set()


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
    assert fake.connects == 1
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake response


async def test_text_stream_cancel_with_the_source_open(fake):
    got_audio = threading.Event()

    async def source():
        yield "First sentence. "
        await asyncio.Event().wait()  # never yields again

    fake.script = lambda _n: [_delta(_pcm([1]))]  # audio, then the server stalls
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
    assert [rid for rid, _ in fake.cancels] == ["resp_1"]


# --- close -------------------------------------------------------------------


def test_close_closes_the_socket_and_the_client(fake):
    module = OpenAIModule(_BASE)
    _say(module)
    module.close()
    assert fake.connections[0].closed.wait(1)
    assert fake.client_closed


def test_close_after_a_reader_failure_closes_the_client(fake):
    module = OpenAIModule(_BASE)
    fake.script = lambda _n: [ConnectionError("reset")]
    with pytest.raises(TTSError):
        _say(module)
    module.close()
    assert fake.client_closed
