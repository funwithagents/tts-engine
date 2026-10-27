"""Tests for TTSEngine."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_engine.config import PlayerConfig, TTSEngineConfig
from tts_engine.engine import TTSEngine
from tts_engine.modules.base import TTSError, TTSOptions


@pytest.fixture
def mock_module():
    module = MagicMock()
    module.synthesize_text = AsyncMock()
    module.synthesize_text_stream = AsyncMock()
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
async def test_say_calls_stream_with_text_and_options(engine, mock_module, mock_player):
    await engine.say("hello world")

    mock_module.synthesize_text.assert_awaited_once()
    args, kwargs = mock_module.synthesize_text.call_args
    assert args[0] == "hello world"
    assert isinstance(args[1], TTSOptions)
    assert kwargs.get("callback") == mock_player.feed


@pytest.mark.asyncio
async def test_say_calls_drain_after_stream(engine, mock_module, mock_player):
    await engine.say("hello")

    mock_module.synthesize_text.assert_awaited_once()
    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_calls_drain_even_on_tts_error(engine, mock_module, mock_player):
    mock_module.synthesize_text.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError):
        await engine.say("hello")

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_propagates_tts_error_after_drain(engine, mock_module, mock_player):
    mock_module.synthesize_text.side_effect = TTSError("synthesis failed")

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

    mock_module.synthesize_text.side_effect = stream

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
    mock_module.synthesize_text.side_effect = _feeding_stream(b"\x01\x00", b"\x02\x00")

    await engine.say("hello")

    assert sink.chunks == [b"\x01\x00", b"\x02\x00"]
    assert sink.drains == 1


@pytest.mark.asyncio
async def test_injected_sink_drained_once_on_tts_error(engine_with_sink, mock_module):
    engine, sink, _ = engine_with_sink
    mock_module.synthesize_text.side_effect = TTSError("synthesis failed")

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

    mock_module.synthesize_text.side_effect = cancelling_stream

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
    fake_module.synthesize_text = AsyncMock()
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
    fake_module.synthesize_text.assert_awaited_once()
    assert fake_module.synthesize_text.call_args.kwargs["callback"] == fake_player.feed
    fake_player.drain.assert_called_once()


# --- say_stream --------------------------------------------------------------


def _consuming_stream(received: list[str], *chunks: bytes):
    """A fake synthesize_text_stream: drains the source into `received`, then
    feeds the given chunks."""

    async def consume(text_stream, options, callback):
        async for piece in text_stream:
            received.append(piece)
        for chunk in chunks:
            callback(chunk)

    return consume


@pytest.mark.asyncio
async def test_say_stream_passes_an_async_iterator_of_pieces_to_the_module(
    engine, mock_module, mock_player
):
    received: list[str] = []
    mock_module.synthesize_text_stream.side_effect = _consuming_stream(
        received, b"\x01\x00"
    )

    async def source():
        yield "Hel"
        yield "lo"

    await engine.say_stream(source())

    assert received == ["Hel", "lo"]
    args, kwargs = mock_module.synthesize_text_stream.call_args
    assert isinstance(args[1], TTSOptions)
    assert kwargs.get("callback") == mock_player.feed
    mock_player.feed.assert_called_once_with(b"\x01\x00")
    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_stream_accepts_a_plain_iterable(engine, mock_module):
    received: list[str] = []
    mock_module.synthesize_text_stream.side_effect = _consuming_stream(received)

    await engine.say_stream(["a", "b"])

    assert received == ["a", "b"]


@pytest.mark.asyncio
async def test_say_stream_rejects_a_bare_string(engine, mock_module, mock_player):
    with pytest.raises(TypeError):
        await engine.say_stream("hello")

    mock_module.synthesize_text_stream.assert_not_called()
    mock_player.drain.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TTSError("synthesis failed"), RuntimeError("llm failed")]
)
async def test_say_stream_drains_once_and_propagates_errors(
    engine, mock_module, mock_player, error
):
    mock_module.synthesize_text_stream.side_effect = error

    with pytest.raises(type(error), match=str(error)):
        await engine.say_stream(["a"])

    mock_player.drain.assert_called_once()


@pytest.mark.asyncio
async def test_say_and_say_stream_share_one_lock(engine, mock_module, mock_player):
    active = 0
    maximum_active = 0

    async def busy(*args, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    mock_module.synthesize_text.side_effect = busy
    mock_module.synthesize_text_stream.side_effect = busy

    await asyncio.gather(engine.say("first"), engine.say_stream(["second"]))

    assert maximum_active == 1
    assert mock_player.drain.call_count == 2


# --- cancellation through the engine, on a real module ----------------------


class _SlowCaptureSink(CaptureSink):
    """A capture sink paced like a sound device, so audio is still flowing
    when the call is cancelled."""

    def feed(self, chunk: bytes) -> None:
        super().feed(chunk)
        time.sleep(0.005)


def _tone_engine(sink: CaptureSink) -> TTSEngine:
    # The real tone module through a capture sink: no fakes in the loop.
    return TTSEngine(
        TTSEngineConfig(module={"type": "tone"}, player=PlayerConfig()), sink=sink
    )


async def _assert_cancel_stops_feeding_and_drains_once(task, sink):
    while not sink.chunks:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = len(sink.chunks)
    await asyncio.sleep(0.05)
    assert len(sink.chunks) == settled
    assert sink.drains == 1


@pytest.mark.asyncio
async def test_cancelling_say_stops_feeding_and_drains_once():
    sink = _SlowCaptureSink()
    engine = _tone_engine(sink)

    task = asyncio.create_task(engine.say("x" * 2000))  # ~100 s of tone
    await _assert_cancel_stops_feeding_and_drains_once(task, sink)


@pytest.mark.asyncio
async def test_cancelling_say_stream_stops_feeding_and_drains_once():
    sink = _SlowCaptureSink()
    engine = _tone_engine(sink)

    async def source():
        yield "x" * 2000
        await asyncio.Event().wait()  # the source stays open

    task = asyncio.create_task(engine.say_stream(source()))
    await _assert_cancel_stops_feeding_and_drains_once(task, sink)


# --- a failing drain never hides the utterance's own outcome -----------------


class _FailingDrainSink(CaptureSink):
    def drain(self) -> None:
        super().drain()
        raise OSError("device gone")


def _engine_with(sink: CaptureSink, mock_module, mocker) -> TTSEngine:
    mocker.patch("tts_engine.engine.load_module", return_value=mock_module)
    mocker.patch("tts_engine.engine.AudioPlayer")
    return TTSEngine(
        TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()), sink=sink
    )


@pytest.mark.asyncio
async def test_synthesis_error_wins_over_a_failing_drain(mock_module, mocker, caplog):
    sink = _FailingDrainSink()
    engine = _engine_with(sink, mock_module, mocker)
    mock_module.synthesize_text.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError, match="synthesis failed"), caplog.at_level("WARNING"):
        await engine.say("hello")
    assert sink.drains == 1
    assert "device gone" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_wins_over_a_failing_drain(mock_module, mocker):
    sink = _FailingDrainSink()
    engine = _engine_with(sink, mock_module, mocker)

    async def cancelling_stream(text, options, callback):
        raise asyncio.CancelledError

    mock_module.synthesize_text_stream.side_effect = cancelling_stream

    with pytest.raises(asyncio.CancelledError):
        await engine.say_stream(["hello"])
    assert sink.drains == 1


@pytest.mark.asyncio
async def test_a_failing_drain_alone_is_reported(mock_module, mocker):
    sink = _FailingDrainSink()
    engine = _engine_with(sink, mock_module, mocker)

    with pytest.raises(OSError, match="device gone"):
        await engine.say("hello")


# --- close -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_the_module_once_and_say_raises_afterwards(
    engine, mock_module
):
    await engine.close()
    await engine.close()

    mock_module.close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed"):
        await engine.say("hello")
    with pytest.raises(RuntimeError, match="closed"):
        await engine.say_stream(["hello"])
    mock_module.synthesize_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_with_closes_the_engine(engine, mock_module):
    async with engine as same:
        assert same is engine
        await engine.say("hello")
    mock_module.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_close_waits_for_the_utterance_in_flight(engine, mock_module):
    release = asyncio.Event()
    order: list[str] = []

    async def slow_synthesis(text, options, callback):
        await release.wait()
        order.append("synthesized")

    mock_module.synthesize_text.side_effect = slow_synthesis
    mock_module.close.side_effect = lambda: order.append("closed")

    say = asyncio.create_task(engine.say("hello"))
    await asyncio.sleep(0)
    close = asyncio.create_task(engine.close())
    await asyncio.sleep(0.01)
    assert not close.done()

    release.set()
    await asyncio.gather(say, close)
    assert order == ["synthesized", "closed"]


# --- source ownership --------------------------------------------------------


class _Source:
    """An async iterator with an `aclose`, like an SDK stream object."""

    def __init__(self, pieces: list[str]) -> None:
        self._pieces = iter(pieces)
        self.closed = 0
        self.close_error: Exception | None = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._pieces)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [None, TTSError("synthesis failed"), asyncio.CancelledError()]
)
async def test_say_stream_closes_the_source_on_every_path(engine, mock_module, outcome):
    source = _Source(["hello"])
    mock_module.synthesize_text_stream.side_effect = outcome

    if outcome is None:
        await engine.say_stream(source)
    else:
        with pytest.raises(type(outcome)):
            await engine.say_stream(source)
    assert source.closed == 1


@pytest.mark.asyncio
async def test_a_failing_source_close_never_hides_the_utterance_error(
    engine, mock_module, caplog
):
    source = _Source(["hello"])
    source.close_error = OSError("stream gone")
    mock_module.synthesize_text_stream.side_effect = TTSError("synthesis failed")

    with pytest.raises(TTSError, match="synthesis failed"), caplog.at_level("WARNING"):
        await engine.say_stream(source)
    assert "stream gone" in caplog.text

    # With no other error, the close failure is the one to report.
    mock_module.synthesize_text_stream.side_effect = None
    with pytest.raises(OSError, match="stream gone"):
        await engine.say_stream(_Source_with_error())


def _Source_with_error() -> _Source:
    source = _Source(["hello"])
    source.close_error = OSError("stream gone")
    return source
