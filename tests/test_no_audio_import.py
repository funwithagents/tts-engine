"""Guard: the engine is usable with a custom sink on a host with no PortAudio.

Runs in a subprocess with `sounddevice` made unimportable, so poisoning
`sys.modules` never leaks into the rest of the suite. Proves that
`import tts_engine`, constructing `TTSEngine(config, sink=...)`, and speaking
through that sink never import sounddevice — and that an engine whose config
discards its audio (`engine.player.discard`) never does either.
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    # Make any `import sounddevice` raise ImportError — as on a host with no
    # PortAudio. If anything tried to import it, the run would fail here.
    sys.modules["sounddevice"] = None

    import tts_engine  # must not import sounddevice
    from tts_engine import TTSEngine
    from tts_engine.config import PlayerConfig, TTSEngineConfig
    from tts_engine.modules import REGISTRY
    from tts_engine.modules.base import TTSModule


    class FakeModule(TTSModule):
        def __init__(self, config):
            pass

        @property
        def sample_rate(self):
            return 24000

        async def synthesize_text(self, text, options, callback):
            callback(b"\\x01\\x00")

        async def synthesize_text_stream(self, text_stream, options, callback):
            async for piece in text_stream:
                callback(piece.encode())


    REGISTRY["fake"] = FakeModule


    class CaptureSink:
        def __init__(self):
            self.chunks = []
            self.drains = 0

        def feed(self, chunk):
            self.chunks.append(chunk)

        def drain(self):
            self.drains += 1


    sink = CaptureSink()
    engine = TTSEngine(
        TTSEngineConfig(module={"type": "fake"}, player=PlayerConfig()), sink=sink
    )
    asyncio.run(engine.say("hi"))

    assert engine.sample_rate == 24000
    assert sink.chunks == [b"\\x01\\x00"]
    assert sink.drains == 1
    # Never imported: still the poison sentinel we planted.
    assert sys.modules["sounddevice"] is None
    print("OK")
    """
)


def test_engine_with_sink_works_without_sounddevice():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


_DISCARD_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys
    import time

    sys.modules["sounddevice"] = None  # as on a host with no PortAudio

    from tts_engine import TTSEngine
    from tts_engine.config import TTSEngineConfig
    from tts_engine.modules import REGISTRY
    from tts_engine.modules.base import TTSModule

    RATE = 16000


    class FakeModule(TTSModule):
        # 0.25 s of audio in 50 ms slices, like a streaming backend.
        def __init__(self, config):
            pass

        @property
        def sample_rate(self):
            return RATE

        async def synthesize_text(self, text, options, callback):
            def run():
                for _ in range(5):
                    callback(bytes(2 * RATE // 20))

            await asyncio.to_thread(run)

        async def synthesize_text_stream(self, text_stream, options, callback):
            raise NotImplementedError


    REGISTRY["fake"] = FakeModule

    engine = TTSEngine(
        TTSEngineConfig.from_dict(
            {"module": {"type": "fake"}, "player": {"discard": True}}
        )
    )
    start = time.monotonic()
    asyncio.run(engine.say("hi"))
    elapsed = time.monotonic() - start

    # The speech lasted as long as its audio, and no device was touched.
    assert 0.25 <= elapsed < 0.25 + 0.5, elapsed
    assert sys.modules["sounddevice"] is None
    print("OK")
    """
)


def test_engine_with_a_discard_config_paces_without_sounddevice():
    result = subprocess.run(
        [sys.executable, "-c", _DISCARD_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
