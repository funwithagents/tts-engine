"""ElevenLabs streaming TTS module."""

import os
import threading
from collections.abc import Callable, Iterator

import miniaudio
from elevenlabs import ElevenLabs
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
)


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


def _unit_interval(config: dict, field: str, default: float) -> float:
    """Read an optional numeric field that must lie in [0, 1]; bools rejected."""
    value = config.get(field, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (0.0 <= value <= 1.0)
    ):
        raise ConfigError(
            f"ElevenLabs module '{field}' must be a number between 0 and 1"
        )
    return float(value)


class ElevenLabsModule(TTSModule):
    def __init__(self, config: dict) -> None:
        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("ElevenLabs module requires a non-empty 'voice_id'")

        model = config.get("model", "eleven_flash_v2_5")
        if not isinstance(model, str) or not model:
            raise ConfigError("ElevenLabs module 'model' must be a non-empty string")

        self._voice_id: str = voice_id
        self._model: str = model
        self._stability: float = _unit_interval(config, "stability", 0.5)
        self._similarity_boost: float = _unit_interval(config, "similarity_boost", 0.75)
        self._client = ElevenLabs(api_key=api_key)

    @property
    def sample_rate(self) -> int:
        # Matches the requested output_format="mp3_44100_128" and the rate
        # miniaudio decodes to below.
        return 44100

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env`` — so a config file can be
        committed with only the env-var name and no secret."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("ElevenLabs module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("ElevenLabs module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"ElevenLabs module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "ElevenLabs module requires a non-empty 'api_key' or 'api_key_env'"
        )

    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _blocking_stream(stop: threading.Event) -> None:
            # Build the provider request and the decoder inside the provider
            # try so SDK/decoder failures become TTSError. (The generator
            # expression evaluates the SDK call eagerly, so it fails here.)
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
                pcm_iter = iter(
                    miniaudio.stream_any(
                        _ChunkSource(raw_chunks),
                        output_format=miniaudio.SampleFormat.SIGNED16,
                        nchannels=1,
                        sample_rate=44100,
                    )
                )
            except Exception as exc:
                raise TTSError(f"ElevenLabs request failed: {exc}") from exc

            while not stop.is_set():
                # Advance the decoder inside the provider try so SDK/decoder
                # errors become TTSError; feed the callback outside it so a
                # downstream playback failure keeps its own identity.
                try:
                    pcm_chunk = next(pcm_iter)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"ElevenLabs request failed: {exc}") from exc
                callback(pcm_chunk.tobytes())

        await run_cancellable_worker(_blocking_stream)
