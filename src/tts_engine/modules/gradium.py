"""Gradium streaming TTS module.

Behind the `gradium` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra becomes
a clear `ConfigError`.

The SDK is async-only (an aiohttp WebSocket), so the cancellable worker thread
runs a private event loop of its own and drives `callback` from there — the
engine's loop never sees the blocking sink. See specs/gradium-module.md,
"Threading model".
"""

import asyncio
import os
import threading
from collections.abc import Callable

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
)

_SAMPLE_RATES = (8000, 16000, 24000, 44100, 48000)

# json_config voice settings: field -> inclusive range. Sent only when set so
# the API's own defaults apply otherwise.
_VOICE_SETTINGS: dict[str, tuple[float, float]] = {
    "temp": (0.0, 1.4),
    "cfg_coef": (1.0, 4.0),
    "padding_bonus": (-4.0, 4.0),
}


def _optional_str(config: dict, field: str) -> str | None:
    """Read an optional field that, when present, must be a non-empty string."""
    value = config.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"Gradium module '{field}' must be a non-empty string")
    return value


def _optional_number(config: dict, field: str, lo: float, hi: float) -> float | None:
    """Read an optional numeric field that must lie in [lo, hi]; bools rejected."""
    value = config.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"Gradium module '{field}' must be a number")
    if not (lo <= value <= hi):
        raise ConfigError(
            f"Gradium module '{field}' must be a number between {lo} and {hi}"
        )
    return float(value)


class GradiumModule(TTSModule):
    def __init__(self, config: dict) -> None:
        try:
            from gradium import GradiumClient
        except ImportError as exc:
            raise ConfigError(
                "The 'gradium' module requires the gradium extra: "
                "pip install tts-engine[gradium]"
            ) from exc

        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("Gradium module requires a non-empty 'voice_id'")

        model = config.get("model", "default")
        if not isinstance(model, str) or not model:
            raise ConfigError("Gradium module 'model' must be a non-empty string")

        sample_rate = config.get("sample_rate", 48000)
        if isinstance(sample_rate, bool) or sample_rate not in _SAMPLE_RATES:
            raise ConfigError(
                "Gradium module 'sample_rate' must be one of "
                + ", ".join(str(r) for r in _SAMPLE_RATES)
            )

        json_config: dict[str, float | str] = {}
        for field, (lo, hi) in _VOICE_SETTINGS.items():
            value = _optional_number(config, field, lo, hi)
            if value is not None:
                json_config[field] = value
        rewrite_rules = _optional_str(config, "rewrite_rules")
        if rewrite_rules is not None:
            json_config["rewrite_rules"] = rewrite_rules

        self._voice_id: str = voice_id
        self._model: str = model
        self._sample_rate: int = sample_rate
        self._json_config = json_config
        self._pronunciation_id = _optional_str(config, "pronunciation_id")

        base_url = _optional_str(config, "base_url")
        # The key is always passed explicitly: the SDK's own GRADIUM_API_KEY
        # fallback is never relied on, so this module's key rules are the only
        # ones in force.
        if base_url is None:
            self._client = GradiumClient(api_key=api_key)
        else:
            self._client = GradiumClient(api_key=api_key, base_url=base_url)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env`` — so a config file can be
        committed with only the env-var name and no secret."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("Gradium module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("Gradium module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"Gradium module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "Gradium module requires a non-empty 'api_key' or 'api_key_env'"
        )

    def _setup(self) -> dict:
        """The per-utterance setup sent to the API; optional parts only when set."""
        setup: dict = {
            "model_name": self._model,
            "voice_id": self._voice_id,
            "output_format": f"pcm_{self._sample_rate}",
        }
        if self._json_config:
            setup["json_config"] = dict(self._json_config)
        if self._pronunciation_id is not None:
            setup["pronunciation_id"] = self._pronunciation_id
        return setup

    async def _consume(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Run one utterance over one WebSocket, feeding audio to `callback`.

        Runs on the worker thread's private loop. SDK/connection failures
        become TTSError; `callback` is invoked outside that boundary so a
        downstream playback failure keeps its own identity.
        """
        try:
            tts_cm = self._client.tts_realtime(
                wait_for_ready_on_start=True, **self._setup()
            )
            tts = await tts_cm.__aenter__()
        except Exception as exc:
            raise TTSError(f"Gradium request failed: {exc}") from exc

        try:
            try:
                ready_rate = (tts.ready or {}).get("sample_rate")
                if ready_rate is not None and ready_rate != self._sample_rate:
                    raise TTSError(
                        f"Gradium stream is {ready_rate} Hz but the module "
                        f"declares {self._sample_rate} Hz"
                    )
                await tts.send_text(text)
                await tts.send_eos()
                stream_iter = tts.__aiter__()
            except TTSError:
                raise
            except Exception as exc:
                raise TTSError(f"Gradium request failed: {exc}") from exc

            while not stop.is_set():
                try:
                    msg = await stream_iter.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"Gradium request failed: {exc}") from exc
                if msg.get("type") == "audio":
                    callback(msg["audio"])
        finally:
            # Closes the socket and session at once, so a cancel mid-utterance
            # drops the request instead of draining the rest of the audio.
            await tts_cm.__aexit__(None, None, None)

    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _blocking_stream(stop: threading.Event) -> None:
            # Private event loop for the async SDK; `callback` runs on this
            # thread, never on the engine's loop (the default sink blocks).
            asyncio.run(self._consume(text, callback, stop))

        await run_cancellable_worker(_blocking_stream)
