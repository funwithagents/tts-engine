"""ElevenLabs streaming TTS module."""
import asyncio
from collections.abc import Iterator

import miniaudio
from elevenlabs import ElevenLabs
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSModule, TTSOptions


class _ChunkSource(miniaudio.StreamableSource):
    """Wraps a bytes-chunk iterator as a miniaudio StreamableSource."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buf = bytearray()

    def read(self, num_bytes: int) -> bytes:
        while len(self._buf) < num_bytes:
            try:
                self._buf.extend(next(self._chunks))
            except StopIteration:
                break
        data = bytes(self._buf[:num_bytes])
        self._buf = self._buf[num_bytes:]
        return data


class ElevenLabsModule(TTSModule):

    def __init__(self, config: dict) -> None:
        api_key = config.get("api_key")
        if not api_key:
            raise ConfigError("ElevenLabs module requires a non-empty 'api_key'")

        voice_id = config.get("voice_id")
        if not voice_id:
            raise ConfigError("ElevenLabs module requires a non-empty 'voice_id'")

        self._voice_id: str = voice_id
        self._model: str = config.get("model", "eleven_flash_v2_5")
        self._stability: float = config.get("stability", 0.5)
        self._similarity_boost: float = config.get("similarity_boost", 0.75)
        self._client = ElevenLabs(api_key=api_key)

    async def stream(self, text: str, options: TTSOptions, callback) -> None:
        def _blocking_stream():
            try:
                raw_chunks = (
                    chunk
                    for chunk in self._client.text_to_speech.stream(
                        text=text,
                        voice_id=self._voice_id,
                        model_id=self._model,
                        output_format="mp3_44100_128",
                        voice_settings=VoiceSettings(
                            stability=self._stability,
                            similarity_boost=self._similarity_boost,
                        ),
                    )
                    if chunk
                )
                source = _ChunkSource(raw_chunks)
                for pcm_chunk in miniaudio.stream_any(
                    source,
                    output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=1,
                    sample_rate=44100,
                ):
                    callback(pcm_chunk.tobytes())
            except Exception as exc:
                raise TTSError(f"ElevenLabs request failed: {exc}") from exc

        await asyncio.to_thread(_blocking_stream)
