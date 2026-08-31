"""TTSEngine: wires module + player, exposes speak()."""

import asyncio

from tts_engine.audio import AudioPlayer
from tts_engine.config import TTSEngineConfig
from tts_engine.modules import load_module
from tts_engine.modules.base import TTSModule, TTSOptions


class TTSEngine:
    def __init__(self, module: TTSModule, player: AudioPlayer) -> None:
        self._module = module
        self._player = player
        self._speak_lock = asyncio.Lock()

    @classmethod
    def from_config(cls, config: TTSEngineConfig) -> "TTSEngine":
        """Build the module and player from config, then construct the engine."""
        module = load_module(config.module)
        player = AudioPlayer(device=config.player.device)
        return cls(module, player)

    async def speak(self, text: str) -> None:
        async with self._speak_lock:
            options = TTSOptions()
            try:
                await self._module.stream(text, options, callback=self._player.feed)
            finally:
                self._player.drain()
