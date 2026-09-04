"""TTSEngine: builds module + sink from config, exposes speak()."""

import asyncio

from tts_engine.audio import AudioPlayer, AudioSink
from tts_engine.config import TTSEngineConfig
from tts_engine.modules import load_module
from tts_engine.modules.base import TTSOptions


class TTSEngine:
    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        """Build the module (via load_module) from config. Use the injected sink,
        or build the default local AudioPlayer from config when sink is None.

        When a sink is injected, no AudioPlayer is constructed and sounddevice is
        never imported — the engine stays usable on hosts with no audio device.
        """
        self._module = load_module(config.module)
        self._sink: AudioSink = (
            sink
            if sink is not None
            else AudioPlayer(
                device=config.player.device, sample_rate=self._module.sample_rate
            )
        )
        self._speak_lock = asyncio.Lock()

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) of the PCM chunks fed to the sink — the active
        module's declared rate. Fixed for the engine's lifetime."""
        return self._module.sample_rate

    async def speak(self, text: str) -> None:
        async with self._speak_lock:
            options = TTSOptions()
            try:
                await self._module.stream(text, options, callback=self._sink.feed)
            finally:
                self._sink.drain()
