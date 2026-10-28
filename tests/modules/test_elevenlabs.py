"""Unit tests for ElevenLabsModule."""

import array as _array
import asyncio
import base64
import json
import queue
import sys
import time
import types
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


V4_CONFIG = {**VALID_CONFIG, "model": "eleven_v4_turbo"}


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # The receive loop polls `stop` on this interval; tests wait on the
    # module's logic, not on wall-clock ticks.
    monkeypatch.setattr(
        "tts_engine.modules.elevenlabs.module._RECV_POLL_SECONDS", 0.005
    )


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
    # The request (and each read of its body) is bounded, not the SDK's 240 s.
    assert kwargs["request_options"] == {"timeout_in_seconds": 30}


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
        "timeout": 2.5,
    }
    kwargs = await _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config)
    assert kwargs["model_id"] == "eleven_multilingual_v2"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.8, similarity_boost=0.9
    )
    # The SDK takes whole seconds: a fractional timeout is rounded up.
    assert kwargs["request_options"] == {"timeout_in_seconds": 3}


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_v4_model_complete_text_goes_over_http_text_to_speech(
    mock_elevenlabs_cls, mock_stream_any
):
    # The socket branches on the model; the complete-text path does not: the
    # text-to-speech endpoint serves every model in one request.
    kwargs = await _provider_stream_kwargs(
        mock_elevenlabs_cls, mock_stream_any, V4_CONFIG
    )
    assert kwargs["model_id"] == "eleven_v4_turbo"
    assert kwargs["voice_id"] == "test-voice-id"


_ABSENT = object()  # an override value that removes the field


@pytest.mark.parametrize(
    "override, field",
    [
        # `voice_id` is checked by the module itself: every shape stays.
        ({"voice_id": _ABSENT}, "voice_id"),
        ({"voice_id": ""}, "voice_id"),
        ({"voice_id": 42}, "voice_id"),
        # Helper-validated fields: one row per field and per bound.
        ({"model": ""}, "model"),
        ({"stability": 5}, "stability"),
        ({"similarity_boost": -0.1}, "similarity_boost"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_field_raises_config_error(override, field):
    config = {**VALID_CONFIG, **override}
    config = {k: v for k, v in config.items() if v is not _ABSENT}
    with pytest.raises(ConfigError, match=field):
        ElevenLabsModule(config)


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
def test_chunk_source_serves_short_reads_without_waiting_for_more_chunks(
    mock_elevenlabs_cls, mock_stream_any
):
    # The decoder asks for far more bytes than one provider chunk holds. The
    # adapter must hand back the first chunk at once rather than pull further
    # chunks to fill the request — otherwise playback waits for ~4 s of audio.
    # Empty provider chunks never reach the decoder.
    pulled: list[bytes] = []

    def provider_chunks():
        for chunk in (b"first", b"", b"second", b"", b"third"):
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

    module = ElevenLabsModule(VALID_CONFIG)
    task = asyncio.create_task(
        module.synthesize_text("hello", TTSOptions(), _signal(loop, first_chunk))
    )
    await first_chunk.wait()
    task.cancel()
    # The worker must honor `stop` between decoded chunks, or this hangs.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


def _signal(loop: asyncio.AbstractEventLoop, event: asyncio.Event):
    """A callback that sets `event` on `loop` from the module's worker thread."""

    def callback(_chunk: bytes) -> None:
        loop.call_soon_threadsafe(event.set)

    return callback


# --- text stream (the module's own stream-input WebSocket loop) --------------


class _FakeStreamInput:
    """Scripted stand-in for either stream-input socket (a `websockets.sync`
    connection as a context manager): records every message sent, answers each
    text message with one audio message carrying the text's bytes, and sends
    the final flag after the end-of-stream message — the way the server
    behaves over time, so tests can observe audio arriving between pieces.

    It speaks both protocols: the text-to-speech socket's (`text` frames, an
    empty `text` ends the stream, `isFinal`) and the text-to-dialogue socket's
    (`voices` opening, `inputs` frames, `close_socket` ends the stream,
    `is_final` preceded by an informational `is_final_audio_for_turn`)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.endless = False  # keep producing audio forever (cancel test)
        self.fail_send: Exception | None = None  # raised by send() on text
        self.fail_recv: Exception | None = None  # raised by recv() once
        # Delivered instead of audio for the first text message: a protocol
        # error payload, or a clean close (the exception to raise).
        self.answer_first_text: dict | Exception | None = None
        self._inbox: queue.Queue = queue.Queue()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self.closed = True
        return False

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if _is_opening(message) or "keep_alive" in message:
            return
        if self.fail_send is not None:
            raise self.fail_send
        if message.get("text") == "":
            self._inbox.put({"isFinal": True})
        elif message.get("close_socket"):
            self._inbox.put({"is_final_audio_for_turn": True})
            self._inbox.put({"is_final": True})
        else:
            if self.answer_first_text is not None:
                answer, self.answer_first_text = self.answer_first_text, None
                self._inbox.put(answer)
                return
            audio = base64.b64encode(_frame_text(message).encode()).decode()
            self._inbox.put({"audio": audio, "isFinal": False})

    def recv(self, timeout: float | None = None) -> str:
        if self.fail_recv is not None:
            exc, self.fail_recv = self.fail_recv, None
            raise exc
        if self.endless:
            time.sleep(0.005)
            return json.dumps({"audio": base64.b64encode(b"x").decode()})
        try:
            item = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    @property
    def texts(self) -> list[str]:
        """The texts sent after the opening frame, in order, on either
        protocol; the end-of-stream frame counts as `""`."""
        return [
            _frame_text(m)
            for m in self.sent[1:]
            if "text" in m or "inputs" in m or "close_socket" in m
        ]


def _is_opening(message: dict) -> bool:
    return message.get("text") == " " or "voices" in message


def _frame_text(message: dict) -> str:
    """The text a frame carries: `text` on the TTS socket, the joined
    `inputs` on the dialogue socket, `""` for either end-of-stream frame."""
    if "inputs" in message:
        return "".join(item["text"] for item in message["inputs"])
    return message.get("text", "")


def _decode_one_pcm_chunk_per_raw_chunk(source, **kwargs):
    # The adapter serves one raw chunk per read when its buffer is empty.
    while source.read(65536):
        yield _array.array("h", [1, 2])


def _stream_input_module(
    mock_elevenlabs_cls, mock_stream_any, mock_connect, config=VALID_CONFIG
):
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
    return ElevenLabsModule(config), ws, connected


def _source(*pieces: str):
    async def source():
        for piece in pieces:
            yield piece

    return source()


class _RawSource:
    """A raw MP3 source with an explicit `close()` (like the SDK's response
    generator), so its release can be asserted rather than left to GC."""

    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self._chunks

    def close(self) -> None:
        self.closed = True


@patch("elevenlabs.ElevenLabs")
async def test_stream_with_no_audio_succeeds_without_building_a_decoder(
    mock_elevenlabs_cls,
):
    # `miniaudio` is deliberately not patched: the real decoder cannot
    # initialize on no data, so this passes only if no decoder is built when
    # the provider sends no audio — the source is still released.
    raw = _RawSource([b"", b""])
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = raw
    module = ElevenLabsModule(VALID_CONFIG)
    callback = MagicMock()

    await module.synthesize_text("hello", TTSOptions(), callback)

    callback.assert_not_called()
    assert raw.closed is True


@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_decoder_failure_still_releases_the_raw_source(
    mock_elevenlabs_cls, mock_stream_any
):
    mock_stream_any.side_effect = RuntimeError("bad mp3")
    raw = _RawSource([b"mp3", b"more"])
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = raw
    module = ElevenLabsModule(VALID_CONFIG)

    with pytest.raises(TTSError, match="bad mp3"):
        await module.synthesize_text("hello", TTSOptions(), MagicMock())
    assert raw.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_with_no_text_feeds_nothing_and_builds_no_decoder(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    # The interface's "an exhausted stream with no text produces no audio and
    # is not an error": no text and no end-of-stream are sent, the connection
    # is dropped, and no decoder (which cannot init on no data) is built.
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    callback = MagicMock()

    await module.synthesize_text_stream(_source("", ""), TTSOptions(), callback)

    callback.assert_not_called()
    assert ws.texts == []
    assert ws.closed is True
    mock_stream_any.assert_not_called()


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
    assert connected["open_timeout"] == 30.0
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


@pytest.mark.parametrize("model", ["eleven_v3", "eleven_v4_turbo"])
@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_with_a_v3_or_v4_model_drives_the_dialogue_socket(
    mock_elevenlabs_cls, mock_stream_any, mock_connect, model
):
    # The text-to-speech socket rejects these models; the module streams them
    # over the text-to-dialogue socket: voice registered in the opening frame,
    # text as `inputs`, `close_socket` to end, `is_final` as the final flag —
    # the informational `is_final_audio_for_turn` before it is ignored.
    module, ws, connected = _stream_input_module(
        mock_elevenlabs_cls,
        mock_stream_any,
        mock_connect,
        {**VALID_CONFIG, "model": model},
    )
    callback = MagicMock()

    asyncio.run(
        module.synthesize_text_stream(
            _source("Hel", "lo wor", "ld, how", " are you"), TTSOptions(), callback
        )
    )

    assert connected["url"] == (
        "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
        f"?model_id={model}&output_format=mp3_44100_128"
    )
    assert connected["additional_headers"] == {"xi-api-key": "test-api-key"}
    assert ws.sent[0] == {
        "voices": ["test-voice-id"],
        "settings": {"stability": 0.5, "similarity": 0.75},
    }
    assert ws.sent[1] == {"inputs": [{"text": "Hello ", "voice_id": "test-voice-id"}]}
    assert ws.texts == ["Hello ", "world, ", "how are ", "you", ""]
    assert ws.sent[-1] == {"close_socket": True}
    assert callback.call_count == 4
    assert ws.closed is True


@pytest.mark.parametrize(
    "config, keeps_alive",
    [(V4_CONFIG, True), (VALID_CONFIG, False)],
    ids=["dialogue", "tts"],
)
@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
async def test_text_stream_keeps_the_dialogue_socket_alive_while_the_source_pauses(
    mock_elevenlabs_cls, mock_stream_any, mock_connect, monkeypatch, config, keeps_alive
):
    # The dialogue socket drops a connection after 20 s without a client frame;
    # while the source pauses (nothing to send, nothing received) the receiver
    # sends `keep_alive`. The text-to-speech socket needs none.
    monkeypatch.setattr(
        "tts_engine.modules.elevenlabs.module._KEEP_ALIVE_SECONDS", 0.02
    )
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect, config
    )
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()

    async def source():
        yield "Hello "
        await got_audio.wait()
        await asyncio.sleep(0.1)  # a pause several keep-alive periods long
        yield "world."

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), _signal(loop, got_audio)),
        timeout=5,
    )

    kinds = ["keep_alive" if "keep_alive" in m else "other" for m in ws.sent]
    if keeps_alive:
        # Sent during the pause: after the first text frame, before the end.
        assert "keep_alive" in kinds[2:-1]
    else:
        assert "keep_alive" not in kinds
    assert ws.texts == ["Hello ", "world.", ""]


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


@pytest.mark.parametrize("failing", ["connect", "recv"])
@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_connect_or_receive_failure_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect, failing
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    if failing == "connect":
        mock_connect.side_effect = OSError("connection failed")
    else:
        ws.fail_recv = RuntimeError("connection failed")

    with pytest.raises(TTSError, match="connection failed"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), MagicMock())
        )
    # A socket that was opened is closed; one that never opened is not entered.
    assert ws.closed is (failing == "recv")


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_protocol_error_message_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    # The API reports a rejected request as a text message with `error` and
    # `message` (then closes): it must surface, not pass for a silent success.
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    ws.answer_first_text = {
        "message": "provider rejected the request",
        "error": "invalid_request",
        "code": 1008,
    }
    callback = MagicMock()

    with pytest.raises(TTSError, match="provider rejected the request"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), callback)
        )
    callback.assert_not_called()
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_clean_close_before_final_message_raises_tts_error(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    from websockets.exceptions import ConnectionClosedOK

    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )
    ws.answer_first_text = ConnectionClosedOK(None, None)

    with pytest.raises(TTSError, match="before its final message"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), MagicMock())
        )
    assert ws.closed is True


@patch("websockets.sync.client.connect")
@patch("miniaudio.stream_any")
@patch("elevenlabs.ElevenLabs")
def test_text_stream_callback_error_propagates_unchanged_and_closes(
    mock_elevenlabs_cls, mock_stream_any, mock_connect
):
    module, ws, _ = _stream_input_module(
        mock_elevenlabs_cls, mock_stream_any, mock_connect
    )

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(
            module.synthesize_text_stream(_source("Hello "), TTSOptions(), bad_callback)
        )
    assert ws.closed is True


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

    async def source():
        yield "Hello "
        await asyncio.Event().wait()  # the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(
            source(), TTSOptions(), _signal(loop, first_chunk)
        )
    )
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

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
    loop = asyncio.get_running_loop()
    opened = asyncio.Event()
    open_socket = mock_connect.side_effect

    def connect(url, **kwargs):
        loop.call_soon_threadsafe(opened.set)
        return open_socket(url, **kwargs)

    mock_connect.side_effect = connect

    async def source():
        await asyncio.Event().wait()  # never yields
        yield ""

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), MagicMock())
    )
    await opened.wait()  # the worker has opened the socket and starts polling
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert ws.closed is True


# --- voice listing -----------------------------------------------------------


def _fake_voice_sdk(monkeypatch, *, voices=(), error: Exception | None = None) -> dict:
    calls: dict = {}

    class Voices:
        def get_all(self, **kwargs):
            calls["get_all"] = kwargs
            if error is not None:
                raise error
            return types.SimpleNamespace(voices=list(voices))

    class ElevenLabs:
        def __init__(self, **kwargs) -> None:
            calls["client"] = kwargs
            self.voices = Voices()

    package = types.ModuleType("elevenlabs")
    package.ElevenLabs = ElevenLabs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "elevenlabs", package)
    return calls


def _el_voice(voice_id, name, labels=None, languages=(), description=None):
    return types.SimpleNamespace(
        voice_id=voice_id,
        name=name,
        labels=labels,
        description=description,
        verified_languages=[
            types.SimpleNamespace(language=lang, locale=locale)
            for lang, locale in languages
        ],
    )


def test_list_voices_maps_labels_and_languages(monkeypatch):
    calls = _fake_voice_sdk(
        monkeypatch,
        voices=[
            _el_voice(
                "v1",
                "River",
                {"gender": "non-binary", "age": "middle_aged", "accent": "american"},
                languages=[("en", "en-US"), ("en", "en-US"), ("fr", None)],
                description="Relaxed",
            ),
            _el_voice("v2", None, {"language": "en", "use_case": "conversational"}),
        ],
    )
    voices = ElevenLabsModule.list_voices(
        {"type": "elevenlabs", "api_key": "k", "timeout": 2.5}
    )

    assert calls["client"] == {"api_key": "k"}
    assert calls["get_all"] == {"request_options": {"timeout_in_seconds": 3}}
    river, second = voices
    assert river.config == {"voice_id": "v1"}
    assert (river.name, river.gender, river.age) == ("River", "neutral", "middle_aged")
    assert river.languages == ("en-US", "fr")
    assert (river.description, river.tags) == ("Relaxed", ("american",))
    assert (second.name, second.languages, second.gender, second.tags) == (
        "v2",
        ("en",),
        None,
        ("conversational",),
    )


def test_list_voices_reports_an_api_error_by_its_message(monkeypatch):
    error = Exception("headers: {...} body: ...")
    error.status_code = 401  # type: ignore[attr-defined]
    error.body = {"detail": {"message": "missing the permission voices_read"}}  # type: ignore[attr-defined]
    _fake_voice_sdk(monkeypatch, error=error)
    with pytest.raises(
        TTSError, match=r"HTTP 401: missing the permission voices_read$"
    ):
        ElevenLabsModule.list_voices({"type": "elevenlabs", "api_key": "k"})


def test_list_voices_needs_a_key_but_no_voice(monkeypatch):
    _fake_voice_sdk(monkeypatch)
    assert ElevenLabsModule.list_voices({"type": "elevenlabs", "api_key": "k"}) == []
    with pytest.raises(ConfigError):
        ElevenLabsModule.list_voices({"type": "elevenlabs"})
