"""Unit tests for OpenAICompatibleModule with the openai SDK faked.

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
from tts_engine.modules.base import TTSError, TTSOptions, VoiceListingUnsupported
from tts_engine.modules.openai_compatible import OpenAICompatibleModule

_BASE = {"type": "openai-compatible", "api_key": "k", "voice": "v"}


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
        if self._backend.raise_on_close is not None:
            raise self._backend.raise_on_close
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
        self.client_closed = False
        self.requests: list[dict] = []
        self.responses: list[_FakeResponse] = []
        self.chunks: list = [_pcm([1, 2]), _pcm([3, -4, 5])]
        self.raise_on_enter: Exception | None = None
        self.raise_on_close: Exception | None = None
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

        def close(self) -> None:
            backend.client_closed = True

    package = types.ModuleType("openai")
    package.OpenAI = OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", package)
    return backend


def _run(coro):
    return asyncio.run(coro)


def _say(
    module: OpenAICompatibleModule, callback=lambda _b: None, text: str = "Hi."
) -> None:
    _run(module.synthesize_text(text, TTSOptions(), callback))


def _samples(pcm: bytes) -> list[int]:
    return np.frombuffer(pcm, dtype="<i2").tolist()


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[openai-compatible\]"):
        OpenAICompatibleModule(_BASE)


# --- config ------------------------------------------------------------------


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_OPENAI_KEY", "from-env")
    OpenAICompatibleModule(
        {"type": "openai-compatible", "api_key_env": "MY_OPENAI_KEY", "voice": "v"}
    )
    assert fake.client_kwargs == {"api_key": "from-env"}


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"voice": None}, "voice"),
        ({"voice": ""}, "voice"),
        ({"model": ""}, "model"),
        ({"instructions": ""}, "instructions"),
        ({"speed": 0.2}, "speed"),
        ({"speed": 4.5}, "speed"),
        ({"base_url": ""}, "base_url"),
        ({"sample_rate": 0, "base_url": "http://x/v1"}, "sample_rate"),
        ({"sample_rate": 24000.0}, "sample_rate"),
        ({"sample_rate": True}, "sample_rate"),
        ({"sample_rate": 44100}, "base_url"),  # OpenAI itself is fixed at 24 kHz
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        OpenAICompatibleModule({**_BASE, **override})


def test_base_url_reaches_the_client_only_when_set(fake):
    OpenAICompatibleModule(_BASE)
    assert fake.client_kwargs == {"api_key": "k"}
    OpenAICompatibleModule({**_BASE, "base_url": "http://localhost:8000/v1"})
    assert fake.client_kwargs == {
        "api_key": "k",
        "base_url": "http://localhost:8000/v1",
    }


def test_defaults_reach_the_request(fake):
    module = OpenAICompatibleModule(_BASE)
    _say(module)
    assert module.sample_rate == 24000
    assert fake.requests == [
        {
            "input": "Hi.",
            "model": "gpt-4o-mini-tts-2025-03-20",
            "voice": "v",
            "response_format": "pcm",
            "timeout": 30.0,
        }
    ]


def test_custom_values_reach_the_request(fake):
    module = OpenAICompatibleModule(
        {
            **_BASE,
            "model": "tts-1",
            "instructions": "Whisper.",
            "speed": 2,
            "timeout": 5,
            "base_url": "http://x/v1",
            "sample_rate": 44100,  # declares a compatible server's rate
        }
    )
    _say(module)
    assert module.sample_rate == 44100
    request = fake.requests[0]
    assert request["model"] == "tts-1"
    assert request["instructions"] == "Whisper."
    assert request["speed"] == 2.0
    assert request["timeout"] == 5.0


# --- synthesize_text ---------------------------------------------------------


def test_chunks_reach_the_callback_unchanged(fake):
    received = bytearray()
    _say(OpenAICompatibleModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [1, 2, 3, -4, 5]
    assert fake.responses[0].closed


@pytest.mark.parametrize(
    ("config", "slice_samples"),
    [
        pytest.param(_BASE, 2400, id="24k"),
        pytest.param(
            {**_BASE, "base_url": "http://x/v1", "sample_rate": 44100},
            4410,
            id="declared-44k1",
        ),
    ],
)
def test_odd_byte_is_carried_over_and_audio_fed_in_100ms_slices(
    fake, config, slice_samples
):
    # 200 ms of audio, the first chunk ending mid-sample past one 100 ms slice,
    # then a lone trailing byte: one feeder spans the utterance, slicing at the
    # declared rate, and the half sample at the end is dropped.
    samples = np.arange(2 * slice_samples) % 1000
    raw = _pcm(samples)
    split = 2 * (slice_samples + slice_samples // 2) + 1
    fake.chunks = [raw[:split], raw[split:], b"\x07"]
    chunks: list[bytes] = []
    _say(OpenAICompatibleModule(config), chunks.append)
    half = slice_samples // 2
    assert [len(c) for c in chunks] == [2 * slice_samples, 2 * half, 2 * half]
    assert _samples(b"".join(chunks)) == samples.tolist()


def test_body_shorter_than_the_wav_check_is_fed(fake):
    fake.chunks = [_pcm([9])]
    received = bytearray()
    _say(OpenAICompatibleModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [9]


def test_blank_text_makes_no_request(fake):
    received = bytearray()
    _say(OpenAICompatibleModule(_BASE), received.extend, text="  \n")
    assert fake.requests == []
    assert received == b""


def test_wav_body_is_tts_error_before_any_feed(fake):
    fake.chunks = [b"RI", b"FF\x24\x00\x00\x00WAVEfmt ", _pcm([1, 2, 3])]
    received = bytearray()
    with pytest.raises(TTSError, match="returned WAV"):
        _say(OpenAICompatibleModule(_BASE), received.extend)
    assert received == b""
    assert fake.responses[0].closed


def test_request_error_is_tts_error(fake):
    fake.raise_on_enter = RuntimeError("Error code: 404 - model_not_found")
    with pytest.raises(
        TTSError, match="OpenAI-compatible request failed: Error code: 404"
    ):
        _say(OpenAICompatibleModule(_BASE))


def test_mid_body_error_is_tts_error_and_closes(fake):
    fake.chunks = [_pcm([1]), ConnectionError("connection reset")]
    with pytest.raises(TTSError, match="connection reset"):
        _say(OpenAICompatibleModule(_BASE))
    assert fake.responses[0].closed


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(OpenAICompatibleModule(_BASE), bad_callback)
    assert fake.responses[0].closed


async def test_cancel_closes_the_response_before_cancelled_error(fake):
    module = OpenAICompatibleModule(_BASE)
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the body, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending chunk return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert fake.responses[0].closed


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_requests_per_sentence_as_the_source_arrives(fake):
    # Split pieces are grouped into sentences, and the first is spoken before
    # the source finishes: it is gated on that sentence's audio.
    module = OpenAICompatibleModule(_BASE)
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
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake response
    assert all(response.closed for response in fake.responses)


def test_close_closes_the_sdk_client(fake):
    OpenAICompatibleModule(_BASE).close()
    assert fake.client_closed


# --- voice listing -----------------------------------------------------------


def test_list_voices_is_unsupported_and_points_to_the_documentation():
    with pytest.raises(
        VoiceListingUnsupported, match=r"https://platform\.openai\.com/"
    ):
        OpenAICompatibleModule.list_voices({"type": "openai-compatible"})


def test_list_voices_another_server_documents_its_own_voices():
    with pytest.raises(
        VoiceListingUnsupported, match=r"server at http://localhost:8880/v1"
    ):
        OpenAICompatibleModule.list_voices(
            {"type": "openai-compatible", "base_url": "http://localhost:8880/v1"}
        )
