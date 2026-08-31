"""TTSEngine: builds module + player from config, exposes speak()."""

import asyncio

from tts_engine.audio import AudioPlayer
from tts_engine.config import TTSEngineConfig
from tts_engine.modules import load_module
from tts_engine.modules.base import TTSOptions


class TTSEngine:
    def __init__(self, config: TTSEngineConfig) -> None:
        """Build the module (via load_module) and player (AudioPlayer) from config."""
        self._module = load_module(config.module)
        self._player = AudioPlayer(device=config.player.device)
        self._speak_lock = asyncio.Lock()

    async def speak(self, text: str) -> None:
        async with self._speak_lock:
            options = TTSOptions()
            try:
                await self._module.stream(text, options, callback=self._player.feed)
            finally:
                self._player.drain()
