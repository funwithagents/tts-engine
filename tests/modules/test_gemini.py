"""Unit tests for GeminiModule with the google-genai SDK faked.

The fast tier never calls the API, so `google` and `google.genai` are faked via
`sys.modules`: `genai.Client(...).models.generate_content_stream` records its
kwargs and returns a real generator over scripted responses, so `close()` and
the lazy first `next()` behave like the SDK's.
"""

import asyncio
import sys
import threading
import types

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.gemini import GeminiModule

_BASE = {"type": "gemini", "api_key": "k"}
# The client's default request timeout (30 s, in the SDK's milliseconds).
_HTTP_OPTIONS = {"http_options": {"timeout": 30000}}
_MIME = "audio/l16; rate=24000; channels=1"


def _pcm(samples) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


def _part(data: bytes | None = None, mime: str = _MIME, text: str | None = None):
    inline = None if data is None else types.SimpleNamespace(data=data, mime_type=mime)
    return types.SimpleNamespace(inline_data=inline, text=text)


def _chunk(*parts, finish_reason=None, block_reason=None):
    candidate = types.SimpleNamespace(
        content=types.SimpleNamespace(parts=list(parts)), finish_reason=finish_reason
    )
    feedback = (
        None
        if block_reason is None
        else types.SimpleNamespace(block_reason=block_reason)
    )
    return types.SimpleNamespace(candidates=[candidate], prompt_feedback=feedback)


_STOP = types.SimpleNamespace(value="STOP")  # stands in for FinishReason.STOP


class _FakeBackend:
    """Shared state behind the fake client: records calls, injects faults."""

    def __init__(self) -> None:
        self.client_kwargs: dict | None = None
        self.client_closed = False
        self.requests: list[dict] = []
        self.closed: list[bool] = []
        self.chunks: list = [
            _chunk(_part(_pcm([0, 100]))),
            _chunk(_part(text="ignored"), _part(_pcm([-100, 32767]))),  # text skipped
            _chunk(_part(_pcm([5])), finish_reason=_STOP),
        ]
        self.raise_on_first: Exception | None = None
        self.raise_after: tuple[int, Exception] | None = None
        self.raise_on_close: Exception | None = None
        self.block: threading.Event | None = None
        self.reached = threading.Event()

    @property
    def texts(self) -> list[str]:
        return [request["contents"] for request in self.requests]

    def stream(self, index: int):
        # A generator function, like the SDK's: nothing runs until `next`.
        try:
            if self.raise_on_first is not None:
                raise self.raise_on_first
            if self.block is not None:
                # Endless stream that waits to be released before each chunk.
                while True:
                    self.reached.set()
                    self.block.wait(timeout=5)
                    yield _chunk(_part(_pcm([1])))
            for n, chunk in enumerate(self.chunks):
                if self.raise_after is not None and n == self.raise_after[0]:
                    raise self.raise_after[1]
                yield chunk
        finally:
            self.closed[index] = True
            if self.raise_on_close is not None:
                raise self.raise_on_close


@pytest.fixture
def fake(monkeypatch) -> _FakeBackend:
    backend = _FakeBackend()

    class Models:
        def generate_content_stream(self, **kwargs):
            backend.requests.append(kwargs)
            backend.closed.append(False)
            return backend.stream(len(backend.closed) - 1)

    class Client:
        def __init__(self, **kwargs) -> None:
            backend.client_kwargs = kwargs
            self.models = Models()

        def close(self) -> None:
            backend.client_closed = True

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.Client = Client  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return backend


def _run(coro):
    return asyncio.run(coro)


def _say(module: GeminiModule, callback=lambda _b: None) -> None:
    _run(module.synthesize_text("Hi.", TTSOptions(), callback))


def _samples(pcm: bytes) -> list[int]:
    return np.frombuffer(pcm, dtype="<i2").tolist()


# --- lazy import gate --------------------------------------------------------


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.genai", None)  # -> ImportError
    with pytest.raises(ConfigError, match=r"tts-engine\[gemini\]"):
        GeminiModule(_BASE)


# --- config ------------------------------------------------------------------


def test_missing_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        GeminiModule({"type": "gemini"})


def test_empty_api_key_raises(fake):
    with pytest.raises(ConfigError, match="api_key"):
        GeminiModule({"type": "gemini", "api_key": ""})


def test_api_key_from_env(fake, monkeypatch):
    monkeypatch.setenv("MY_GEMINI_KEY", "from-env")
    GeminiModule({"type": "gemini", "api_key_env": "MY_GEMINI_KEY"})
    assert fake.client_kwargs == {"api_key": "from-env", **_HTTP_OPTIONS}


def test_literal_api_key_takes_precedence_over_env(fake, monkeypatch):
    monkeypatch.setenv("MY_GEMINI_KEY", "from-env")
    GeminiModule({**_BASE, "api_key_env": "MY_GEMINI_KEY"})
    assert fake.client_kwargs == {"api_key": "k", **_HTTP_OPTIONS}


def test_api_key_env_pointing_to_unset_var_raises(fake, monkeypatch):
    monkeypatch.delenv("MY_GEMINI_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_GEMINI_KEY"):
        GeminiModule({"type": "gemini", "api_key_env": "MY_GEMINI_KEY"})


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"api_key": 3}, "api_key"),
        ({"api_key": None, "api_key_env": 3}, "api_key_env"),
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"voice": ""}, "voice"),
        ({"voice": 3}, "voice"),
        ({"language_code": ""}, "language_code"),
        ({"language_code": ["en-US"]}, "language_code"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": "30"}, "timeout"),
        ({"timeout": float("inf")}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(fake, override, field):
    with pytest.raises(ConfigError, match=field):
        GeminiModule({**_BASE, **override})


def test_timeout_reaches_the_client_in_milliseconds(fake):
    GeminiModule({**_BASE, "timeout": 2.5})
    assert fake.client_kwargs == {"api_key": "k", "http_options": {"timeout": 2500}}


def test_sample_rate_is_24k(fake):
    assert GeminiModule(_BASE).sample_rate == 24000


def test_defaults_reach_the_request(fake):
    _say(GeminiModule(_BASE))
    # No voice or language set: no speech_config at all, the API's defaults apply.
    assert fake.requests == [
        {
            "model": "gemini-3.8-flash-tts",
            "contents": "Hi.",
            "config": {"response_modalities": ["AUDIO"]},
        }
    ]


def test_custom_values_reach_the_request(fake):
    module = GeminiModule(
        {**_BASE, "model": "gemini-x-tts", "voice": "Kore", "language_code": "fr-FR"}
    )
    _say(module)
    assert fake.requests[0]["model"] == "gemini-x-tts"
    assert fake.requests[0]["config"]["speech_config"] == {
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}},
        "language_code": "fr-FR",
    }


def test_language_alone_sends_no_voice_config(fake):
    _say(GeminiModule({**_BASE, "language_code": "en-US"}))
    assert fake.requests[0]["config"]["speech_config"] == {"language_code": "en-US"}


# --- synthesize_text ---------------------------------------------------------


def test_audio_parts_reach_the_callback_unchanged(fake):
    chunks: list[bytes] = []
    _say(GeminiModule(_BASE), chunks.append)
    assert [_samples(c) for c in chunks] == [[0, 100], [-100, 32767], [5]]
    assert fake.closed == [True]


def test_sample_split_across_parts_is_carried_over(fake):
    raw = _pcm([1000, -1000, 7])
    fake.chunks = [_chunk(_part(raw[:3])), _chunk(_part(raw[3:]))]
    received = bytearray()
    _say(GeminiModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [1000, -1000, 7]


@pytest.mark.parametrize(
    "mime",
    [
        "audio/mpeg",
        "audio/l16; rate=16000; channels=1",
        "audio/l16; rate=24000; channels=2",  # stereo would play as noise
        None,
    ],
)
def test_unexpected_mime_type_is_tts_error(fake, mime):
    fake.chunks = [_chunk(_part(_pcm([1]), mime=mime))]
    with pytest.raises(TTSError, match="unexpected audio format"):
        _say(GeminiModule(_BASE))
    assert fake.closed == [True]


@pytest.mark.parametrize("mime", ["audio/L16", "audio/l16; channels=1; rate=24000"])
def test_mono_24k_mime_is_accepted_with_or_without_parameters(fake, mime):
    fake.chunks = [_chunk(_part(_pcm([1]), mime=mime))]
    received = bytearray()
    _say(GeminiModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [1]


def test_non_stop_finish_reason_is_tts_error_after_the_audio(fake):
    fake.chunks = [_chunk(_part(_pcm([1, 2])), finish_reason="SAFETY")]
    received = bytearray()
    with pytest.raises(TTSError, match="SAFETY"):
        _say(GeminiModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [1, 2]


def test_block_reason_is_tts_error(fake):
    fake.chunks = [_chunk(block_reason="PROHIBITED_CONTENT")]
    with pytest.raises(TTSError, match="PROHIBITED_CONTENT"):
        _say(GeminiModule(_BASE))


def test_error_on_first_next_is_tts_error_and_closes(fake):
    # The SDK sends the request on the first `next`: a bad key, voice or model
    # surfaces there.
    fake.raise_on_first = RuntimeError(
        "400 INVALID_ARGUMENT. No matching speaker voice"
    )
    with pytest.raises(TTSError, match="Gemini request failed: 400 INVALID_ARGUMENT"):
        _say(GeminiModule(_BASE))
    assert fake.closed == [True]


def test_mid_stream_error_is_tts_error_and_closes(fake):
    fake.raise_after = (1, ConnectionError("connection reset"))
    received = bytearray()
    with pytest.raises(TTSError, match="connection reset"):
        _say(GeminiModule(_BASE), received.extend)
    assert _samples(bytes(received)) == [0, 100]
    assert fake.closed == [True]


def test_callback_error_propagates_unchanged_and_closes(fake):
    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(GeminiModule(_BASE), bad_callback)
    assert fake.closed == [True]


def test_callback_error_wins_over_a_failing_close(fake):
    fake.raise_on_close = RuntimeError("connection teardown failed")

    def bad_callback(_b: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        _say(GeminiModule(_BASE), bad_callback)
    assert fake.closed == [True]


async def test_cancel_closes_the_stream_before_cancelled_error(fake):
    module = GeminiModule(_BASE)
    fake.block = threading.Event()
    task = asyncio.create_task(
        module.synthesize_text("Hi.", TTSOptions(), lambda _b: None)
    )
    # Wait (off the loop) until the worker is inside the stream, then cancel.
    await asyncio.to_thread(fake.reached.wait, 5)
    task.cancel()
    fake.block.set()  # let the pending chunk return so the worker sees `stop`
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed == [True]


# --- synthesize_text_stream --------------------------------------------------


def test_text_stream_requests_per_sentence(fake):
    async def source():
        for piece in ["Hello wor", "ld. How are", " you? Fine"]:
            yield piece

    received = bytearray()
    _run(
        GeminiModule(_BASE).synthesize_text_stream(
            source(), TTSOptions(), received.extend
        )
    )

    assert fake.texts == ["Hello world.", "How are you?", "Fine"]
    assert len(received) == 3 * 2 * 5  # five int16 samples per fake stream
    assert fake.closed == [True, True, True]


async def test_text_stream_feeds_first_sentence_before_the_source_finishes(fake):
    module = GeminiModule(_BASE)
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
    module = GeminiModule(_BASE)
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
    assert fake.closed == [True]
    assert fake.texts == ["Hi."]


def test_close_closes_the_sdk_client(fake):
    GeminiModule(_BASE).close()
    assert fake.client_closed
