"""Tests for TTSEngine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_engine.config import PlayerConfig, TTSEngineConfig
from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError, TTSOptions


@pytest.fixture
def mock_module():
    module = MagicMock()
    module.stream = AsyncMock()
    return module


@pytest.fixture
def mock_player():
    player = MagicMock()
    return player


@pytest.fixture
def engine(mock_module, mock_player, mocker):
    # The constructor builds the module and player from config; patch both so the
    # engine's say/drain behavior can be exercised with fakes, no audio hardware.
    mocker.patch("tts_engine.engine.load_module", return_value=mock_module)
    mocker.patch("tts_engine.engine.AudioPlayer", return_value=mock_player)
    return TTSEngine(TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()))


@pytest.mark.asyncio
async def test_say_calls_stream_with_text_and_options(
    engine, mock_module, mock_player
):
    await engine.say("hello world")

    mock_module.stream.assert_awaited_once()
    args, kwargs = mock_module.stream.call_args
    assert args[0] == "hello world"
    assert isinstance(args[1], TTSOptions)
    assert kwargs.get("callback") == mock_player.feed


@pytest.mark.asyncio
async def test_say_calls_drain_after_stream(engine, mock_module, mock_player):
    await engine.say("hello")

    mock_module.stream.assert_awaited_once()
    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_calls_drain_even_on_tts_error(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError):
        await engine.say("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_propagates_tts_error_after_drain(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError, match="synthesis failed"):
        await engine.say("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_say_calls_are_serialized(engine, mock_module, mock_player):
    active = 0
    maximum_active = 0

    async def stream(*args, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    mock_module.stream.side_effect = stream

    await asyncio.gather(engine.say("first"), engine.say("second"))

    assert maximum_active == 1
    assert mock_player.drain.call_count == 2


class CaptureSink:
    """In-memory AudioSink: records fed chunks and drain calls."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.drains = 0

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def drain(self) -> None:
        self.drains += 1


def _feeding_stream(*chunks: bytes):
    async def stream(text, options, callback):
        for chunk in chunks:
            callback(chunk)

    return stream


@pytest.fixture
def engine_with_sink(mock_module, mocker):
    # No AudioPlayer needed when a sink is injected — build the module only.
    mocker.patch("tts_engine.engine.load_module", return_value=mock_module)
    audio_player = mocker.patch("tts_engine.engine.AudioPlayer")
    sink = CaptureSink()
    engine = TTSEngine(
        TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()), sink=sink
    )
    return engine, sink, audio_player


@pytest.mark.asyncio
async def test_injected_sink_receives_chunks_and_one_drain(
    engine_with_sink, mock_module
):
    engine, sink, _ = engine_with_sink
    mock_module.stream.side_effect = _feeding_stream(b"\x01\x00", b"\x02\x00")

    await engine.say("hello")

    assert sink.chunks == [b"\x01\x00", b"\x02\x00"]
    assert sink.drains == 1


@pytest.mark.asyncio
async def test_injected_sink_drained_once_on_tts_error(engine_with_sink, mock_module):
    engine, sink, _ = engine_with_sink
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError):
        await engine.say("hello")

    assert sink.drains == 1


@pytest.mark.asyncio
async def test_injected_sink_drained_once_on_cancellation(
    engine_with_sink, mock_module
):
    engine, sink, _ = engine_with_sink

    async def cancelling_stream(text, options, callback):
        raise asyncio.CancelledError

    mock_module.stream.side_effect = cancelling_stream

    with pytest.raises(asyncio.CancelledError):
        await engine.say("hello")

    assert sink.drains == 1


@pytest.mark.asyncio
async def test_injecting_sink_does_not_construct_audio_player(engine_with_sink):
    _, _, audio_player = engine_with_sink
    audio_player.assert_not_called()


def test_sample_rate_exposes_module_rate(mock_module, mocker):
    mock_module.sample_rate = 24000
    mocker.patch("tts_engine.engine.load_module", return_value=mock_module)
    engine = TTSEngine(
        TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()),
        sink=CaptureSink(),
    )

    assert engine.sample_rate == 24000


@pytest.mark.asyncio
async def test_constructor_wires_module_and_player_from_config(mocker):
    fake_module = MagicMock()
    fake_module.stream = AsyncMock()
    fake_module.sample_rate = 24000
    fake_player = MagicMock()
    load_module = mocker.patch(
        "tts_engine.engine.load_module", return_value=fake_module
    )
    audio_player = mocker.patch(
        "tts_engine.engine.AudioPlayer", return_value=fake_player
    )

    cfg = TTSEngineConfig(
        module={"type": "fake", "k": "v"}, player=PlayerConfig(device=3)
    )
    engine = TTSEngine(cfg)

    load_module.assert_called_once_with({"type": "fake", "k": "v"})
    # The player is opened at the module's declared sample rate.
    audio_player.assert_called_once_with(device=3, sample_rate=24000)

    # The engine drives the config's module and player.
    await engine.say("hi")
    fake_module.stream.assert_awaited_once()
    assert fake_module.stream.call_args.kwargs["callback"] == fake_player.feed
    fake_player.drain.assert_called_once()
