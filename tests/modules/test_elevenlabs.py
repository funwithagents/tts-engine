"""Unit tests for ElevenLabsModule."""

import array as _array
import asyncio
import base64
import json
import queue
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.elevenlabs import ElevenLabsModule

VALID_CONFIG = {
    "type": "elevenlabs",
    "api_key": "test-api-key",
    "voice_id": "test-voice-id",
}


def test_missing_api_key_raises():
    with pytest.raises(ConfigError, match="api_key"):
        ElevenLabsModule({"type": "elevenlabs", "voice_id": "v"})


def test_empty_api_key_raises():
    with pytest.raises(ConfigError, match="api_key"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "", "voice_id": "v"})


def test_missing_voice_id_raises():
    with pytest.raises(ConfigError, match="voice_id"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "k"})


def test_empty_voice_id_raises():
    with pytest.raises(ConfigError, match="voice_id"):
        ElevenLabsModule({"type": "elevenlabs", "api_key": "k", "voice_id": ""})


def test_missing_extra_raises_config_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "elevenlabs", None)  # -> ImportError
    monkeypatch.setitem(sys.modules, "miniaudio", None)
    with pytest.raises(ConfigError, match=r"tts-engine\[elevenlabs\]"):
        ElevenLabsModule(VALID_CONFIG)


@patch("elevenlabs.ElevenLabs")
def test_api_key_from_env(mock_elevenlabs_cls, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    config = {"type": "elevenlabs", "api_key_env": "MY_TTS_KEY", "voice_id": "v"}
    ElevenLabsModule(config)
    mock_elevenlabs_cls.assert_called_once_with(api_key="env-secret")


@patch("elevenlabs.ElevenLabs")
def test_literal_api_key_takes_precedence_over_env(mock_elevenlabs_cls, monkeypatch):
    monkeypatch.setenv("MY_TTS_KEY", "env-secret")
    config = {
        "type": "elevenlabs",
        "api_key": "literal-key",
        "api_key_env": "MY_TTS_KEY",
        "voice_id": "v",
    }
    ElevenLabsModule(config)
    mock_elevenlabs_cls.assert_called_once_with(api_key="literal-key")


def test_api_key_env_pointing_to_unset_var_raises(monkeypatch):
    monkeypatch.delenv("MY_TTS_KEY", raising=False)
    config = {"type": "elevenlabs", "api_key_env": "MY_TTS_KEY", "voice_id": "v"}
    with pytest.raises(ConfigError, match="MY_TTS_KEY"):
        ElevenLabsModule(config)


async def _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config) -> dict:
    """Run one stream() and return the kwargs the module sent to the SDK.

    Provider configuration is observed through the arguments sent to the
    provider client (testing.md), never through private attributes.
    """
    mock_stream_any.return_value = iter([])
    mock_client = mock_elevenlabs_cls.return_value
    mock_client.text_to_speech.stream.return_value = iter([b"mp3"])
    module = ElevenLabsModule(config)
    await module.synthesize_text("hello", TTSOptions(), MagicMock())
    return mock_client.text_to_speech.stream.call_args.kwargs


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_defaults_reach_the_provider_client(mock_elevenlabs_cls, mock_stream_any):
    kwargs = await _provider_stream_kwargs(
        mock_elevenlabs_cls, mock_stream_any, VALID_CONFIG
    )
    assert kwargs["voice_id"] == "test-voice-id"
    assert kwargs["model_id"] == "eleven_flash_v2_5"
    assert kwargs["output_format"] == "mp3_44100_128"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.5, similarity_boost=0.75
    )


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_custom_values_reach_the_provider_client(
    mock_elevenlabs_cls, mock_stream_any
):
    config = {
        **VALID_CONFIG,
        "model": "eleven_multilingual_v2",
        "stability": 0.8,
        "similarity_boost": 0.9,
    }
    kwargs = await _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config)
    assert kwargs["model_id"] == "eleven_multilingual_v2"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.8, similarity_boost=0.9
    )


@pytest.mark.parametrize(
    "override, field",
    [
        ({"api_key": 123}, "api_key"),
        ({"api_key_env": 5}, "api_key_env"),
        ({"voice_id": 42}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 7}, "model"),
        ({"stability": 5}, "stability"),
        ({"stability": True}, "stability"),
        ({"similarity_boost": -0.1}, "similarity_boost"),
        ({"similarity_boost": "x"}, "similarity_boost"),
    ],
)
def test_invalid_field_raises_config_error(override, field):
    with pytest.raises(ConfigError, match=field):
        ElevenLabsModule({**VALID_CONFIG, **override})


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_stream_calls_callback_with_decoded_pcm_bytes(
    mock_elevenlabs_cls, mock_stream_any
):
    pcm_chunks = [_array.array("h", [10, 20]), _array.array("h", [30, 40])]
    mock_client = MagicMock()
    mock_client.text_to_speech.stream.return_value = iter([b"mp3data"])
    mock_elevenlabs_cls.return_value = mock_client
    mock_stream_any.return_value = iter(pcm_chunks)

    module = ElevenLabsModule(VALID_CONFIG)
    callback = MagicMock()
    asyncio.run(module.synthesize_text("hello", TTSOptions(), callback))

    assert callback.call_count == 2
    callback.assert_any_call(pcm_chunks[0].tobytes())
    callback.assert_any_call(pcm_chunks[1].tobytes())


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_stream_skips_empty_elevenlabs_chunks(mock_elevenlabs_cls, mock_stream_any):
    # Drain the _ChunkSource via read() to inspect what bytes reached miniaudio
    all_data = bytearray()

    def capture_source(source, **kwargs):
        while chunk := source.read(1024):
            all_data.extend(chunk)
        return iter([])

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.return_value = iter([b"data", b"", b"more"])
    mock_elevenlabs_cls.return_value = mock_client
    mock_stream_any.side_effect = capture_source

    module = ElevenLabsModule(VALID_CONFIG)
    asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))

    assert bytes(all_data) == b"datamore"


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_chunk_source_serves_short_reads_without_waiting_for_more_chunks(
    mock_elevenlabs_cls, mock_stream_any
):
    # The decoder asks for far more bytes than one provider chunk holds. The
    # adapter must hand back the first chunk at once rather than pull further
    # chunks to fill the request — otherwise playback waits for ~4 s of audio.
    pulled: list[bytes] = []

    def provider_chunks():
        for chunk in (b"first", b"second", b"third"):
            pulled.append(chunk)
            yield chunk

    reads: list[bytes] = []

    def capture_source(source, **kwargs):
        reads.append(source.read(65536))
        assert pulled == [b"first"]  # the request was not filled from later chunks
        while chunk := source.read(65536):
            reads.append(chunk)
        return iter([])

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.return_value = provider_chunks()
    mock_elevenlabs_cls.return_value = mock_client
    mock_stream_any.side_effect = capture_source

    module = ElevenLabsModule(VALID_CONFIG)
    asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))

    assert reads == [b"first", b"second", b"third"]


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_stream_wraps_elevenlabs_exception_in_tts_error(
    mock_elevenlabs_cls, mock_stream_any
):
    # Trigger source consumption so the SDK exception propagates through _ChunkSource.read()
    mock_stream_any.side_effect = lambda source, **kwargs: source.read(1) and iter([])

    mock_client = MagicMock()
    mock_client.text_to_speech.stream.side_effect = RuntimeError("network failure")
    mock_elevenlabs_cls.return_value = mock_client

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(TTSError, match="network failure"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), MagicMock()))


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_stream_does_not_mask_callback_errors(mock_elevenlabs_cls, mock_stream_any):
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])
    mock_stream_any.return_value = iter([_array.array("h", [1, 2])])

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.synthesize_text("hello", TTSOptions(), bad_callback))


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_cancel_stops_callbacks_before_stream_raises(
    mock_elevenlabs_cls, mock_stream_any
):
    # An endless decoder: without cooperative cancellation the worker thread
    # would keep calling back forever after stream() has raised.
    def endless_chunks(*args, **kwargs):
        while True:
            time.sleep(0.005)
            yield _array.array("h", [1])

    mock_stream_any.side_effect = endless_chunks
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    module = ElevenLabsModule(VALID_CONFIG)
    task = asyncio.create_task(module.synthesize_text("hello", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)  # ~10 chunk periods: a leaked worker would show
    assert count == settled


def _signal(loop: asyncio.AbstractEventLoop, event: asyncio.Event):
    """A callback that sets `event` on `loop` from the module's worker thread."""

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(event.set)

    return callback


# --- text stream (the module's own stream-input WebSocket loop) --------------


class _FakeStreamInput:
    """Scripted stand-in for the stream-input socket (a `websockets.sync`
    connection as a context manager): records every message sent, answers each
    text message with one audio message carrying the text's bytes, and sends
    `isFinal` after the end-of-stream message — the way the server behaves over
    time, so tests can observe audio arriving between pieces."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.endless = False  # keep producing audio forever (cancel test)
        self.fail_send: Exception | None = None  # raised by send() on text
        self.fail_recv: Exception | None = None  # raised by recv() once
        self._inbox: queue.Queue = queue.Queue()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self.closed = True
        return False

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        text = message.get("text")
        if text == " ":
            return  # the opening message
        if self.fail_send is not None:
            raise self.fail_send
        if text == "":
            self._inbox.put({"isFinal": True})
        elif text:
            audio = base64.b64encode(text.encode()).decode()
            self._inbox.put({"audio": audio, "isFinal": False})

    def recv(self, timeout: float | None = None) -> str:
        if self.fail_recv is not None:
            exc, self.fail_recv = self.fail_recv, None
            raise exc
        if self.endless:
            time.sleep(0.005)
            return json.dumps({"audio": base64.b64encode(b"x").decode()})
        try:
            return json.dumps(self._inbox.get(timeout=timeout))
        except queue.Empty:
            raise TimeoutError from None

    @property
    def texts(self) -> list[str]:
        """The text messages after the opening one, in order."""
        return [m["text"] for m in self.sent[1:] if "text" in m]


def _decode_one_pcm_chunk_per_raw_chunk(source, **kwargs):
    # The adapter serves one raw chunk per read when its buffer is empty.
    while source.read(65536):
        yield _array.array("h", [1, 2])


def _stream_input_module(mock_elevenlabs_cls, mock_stream_any, mock_connect):
    """An ElevenLabsModule whose stream-input socket is a `_FakeStreamInput`;
    returns (module, socket, recorded connect kwargs)."""
    ws = _FakeStreamInput()
    connected: dict = {}

    def connect(url, **kwargs):
        connected["url"] = url
        connected.update(kwargs)
        return ws

    mock_connect.side_effect = connect
    mock_elevenlabs_cls.return_value = MagicMock()
    mock_stream_any.side_effect = _decode_one_pcm_chunk_per_raw_chunk
    return ElevenLabsModule(VALID_CONFIG), ws, connected


def _source(*pieces: str):
    async def source():
        for piece in pieces:
            yield piece

    return source()


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_sends_word_groups_then_eos_over_stream_input(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, connected = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    callback = MagicMock()

    asyncio.run(
        module.synthesize_text_stream(
            _source("Hel", "lo wor", "ld, how", " are you"), TTSOptions(), callback
        )
    )

    assert connected["url"] == (
        "wss://api.elevenlabs.io/v1/text-to-speech/test-voice-id/stream-input"
        "?model_id=eleven_flash_v2_5&output_format=mp3_44100_128"
    )
    assert connected["additional_headers"] == {"xi-api-key": "test-api-key"}
    assert ws.sent[0] == {
        "text": " ",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        "generation_config": {"chunk_length_schedule": [50]},
    }
    # Forwarded up to the last whitespace, so a sub-word token never goes
    # alone; the remainder, then the end-of-stream message.
    assert ws.texts == ["Hello ", "world, ", "how are ", "you", ""]
    assert all(m.get("try_trigger_generation") for m in ws.sent[1:-1])
    assert callback.call_count == 4  # one decoded chunk per audio message
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_feeds_audio_before_the_source_finishes(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello "
        await got_audio.wait()
        yield "world."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), _signal(loop, got_audio)),
        timeout=5,
    )
    assert ws.texts == ["Hello ", "world.", ""]


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_source_error_propagates_unchanged(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello "
        await got_audio.wait()  # fail once the socket is open and audio flowed
        raise RuntimeError("llm failed")

    with pytest.raises(RuntimeError, match="llm failed"):
        await asyncio.wait_for(
            module.synthesize_text_stream(
                source(), TTSOptions(), _signal(loop, got_audio)
            ),
            timeout=5,
        )
    # Aborted: dropped without an end-of-stream message, socket closed.
    assert "" not in ws.texts
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_sender_failure_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    ws.fail_send = RuntimeError("socket closed")

    with pytest.raises(TTSError, match="socket closed"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), MagicMock())
        )
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_receive_failure_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    ws.fail_recv = RuntimeError("connection reset")

    with pytest.raises(TTSError, match="connection reset"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), MagicMock())
        )
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_connect_failure_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    mock_connect.side_effect = OSError("connection refused")
    mock_stream_any.side_effect = _decode_one_pcm_chunk_per_raw_chunk
    module = ElevenLabsModule(VALID_CONFIG)

    with pytest.raises(TTSError, match="connection refused"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), MagicMock())
        )


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_cancel_mid_audio_stops_callbacks_before_raising(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    # Endless audio from the server while the sender is still blocked waiting
    # for text (the source stays open): cancel must stop the receiver, close
    # the socket, and never feed again once CancelledError has surfaced.
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    ws.endless = True
    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    async def source():
        yield "Hello "
        await asyncio.Event().wait()  # the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    settled = count
    await asyncio.sleep(0.05)  # ~10 chunk periods: a leaked worker would show
    assert count == settled
    assert ws.closed is True
    assert "" not in ws.texts  # dropped, not finished


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_cancel_while_the_server_is_silent_stops_promptly(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    # No audio yet and no text coming (the LLM is thinking): the receiver must
    # be polling `stop` rather than blocked on the socket.
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )

    async def source():
        await asyncio.Event().wait()  # never yields
        yield ""

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), MagicMock())
    )
    await asyncio.sleep(0.05)  # let the worker open the socket and start polling
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert ws.closed is True
