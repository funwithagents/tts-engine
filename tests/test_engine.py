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
    # engine's speak/drain behavior can be exercised with fakes, no audio hardware.
    mocker.patch("tts_engine.engine.load_module", return_value=mock_module)
    mocker.patch("tts_engine.engine.AudioPlayer", return_value=mock_player)
    return TTSEngine(TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()))


@pytest.mark.asyncio
async def test_speak_calls_stream_with_text_and_options(
    engine, mock_module, mock_player
):
    await engine.speak("hello world")

    mock_module.stream.assert_awaited_once()
    args, kwargs = mock_module.stream.call_args
    assert args[0] == "hello world"
    assert isinstance(args[1], TTSOptions)
    assert kwargs.get("callback") == mock_player.feed


@pytest.mark.asyncio
async def test_speak_calls_drain_after_stream(engine, mock_module, mock_player):
    await engine.speak("hello")

    mock_module.stream.assert_awaited_once()
    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_speak_calls_drain_even_on_tts_error(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError):
        await engine.speak("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_speak_propagates_tts_error_after_drain(engine, mock_module, mock_player):
    mock_module.stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError, match="synthesis failed"):
        await engine.speak("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_speak_calls_are_serialized(engine, mock_module, mock_player):
    active = 0
    maximum_active = 0

    async def stream(*args, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    mock_module.stream.side_effect = stream

    await asyncio.gather(engine.speak("first"), engine.speak("second"))

    assert maximum_active == 1
    assert mock_player.drain.call_count == 2


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
    await engine.speak("hi")
    fake_module.stream.assert_awaited_once()
    assert fake_module.stream.call_args.kwargs["callback"] == fake_player.feed
    fake_player.drain.assert_called_once()
