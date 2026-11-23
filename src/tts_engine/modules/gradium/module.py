"""Gradium streaming TTS module.

Behind the `gradium` extra: the SDK is imported lazily inside `__init__` so
`import tts_engine` and `load_module` never require it — a missing extra becomes
a clear `ConfigError`.

The SDK is async-only (an aiohttp WebSocket), so the cancellable worker thread
runs a private event loop of its own and drives `callback` from there — the
engine's loop never sees the blocking sink. See specs/modules/gradium.md,
"Threading model".

A text stream is forwarded as it arrives: on the private loop a sender task
pushes word groups to the socket while the receiver feeds audio back, so this
is a true stream-to-stream pass over one connection.
"""

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    VoiceInfo,
    finite_number,
    normalize_gender,
    optional_str,
    resolve_api_key,
    run_cancellable_worker,
    run_text_stream_worker,
    split_at_last_space,
)
from tts_engine.modules.fields import Field

log = logging.getLogger(__name__)

_SAMPLE_RATES = (8000, 16000, 24000, 44100, 48000)
_DEFAULT_TIMEOUT = 30.0
_LABEL = "Gradium module"
# How often the receive loop (and the connection setup) wakes to poll `stop`
# while the server is silent, so a cancel is honored promptly.
_RECV_POLL_SECONDS = 0.1

# json_config voice settings: field -> inclusive range. Sent only when set so
# the API's own defaults apply otherwise.
_VOICE_SETTINGS: dict[str, tuple[float, float]] = {
    "temp": (0.0, 1.4),
    "cfg_coef": (1.0, 4.0),
    "padding_bonus": (-4.0, 4.0),
}


class GradiumModule(TTSModule):
    kind = "hosted"
    title = "Gradium"
    extra = "gradium"
    fields = (
        Field(
            "voice_id",
            "string",
            "The id of the voice to speak with, from Gradium's catalog or a "
            "custom voice of the account.",
            required=True,
            message="Gradium module requires a non-empty 'voice_id'",
        ),
        Field(
            "model",
            "string",
            "The Gradium model alias, 'default' or 'gradium-tts-beta'.",
            default="default",
        ),
        Field(
            "api_key_env",
            "string",
            "The environment variable holding the Gradium API key.",
            allow_empty=True,
            secret="env",
            message="Gradium module 'api_key_env' must be a string",
        ),
        Field(
            "api_key",
            "string",
            "The Gradium API key itself. Prefer 'api_key_env', which keeps the "
            "key out of the config file.",
            allow_empty=True,
            secret="literal",
            message="Gradium module 'api_key' must be a string",
        ),
        Field(
            "sample_rate",
            "integer",
            "The sample rate of the audio in Hz. 48000 is the voice's native "
            "rate; the service resamples to the others.",
            default=48000,
            choices=_SAMPLE_RATES,
            advanced=True,
        ),
        Field(
            "temp",
            "number",
            "The sampling temperature, from 0 (steadier) to 1.4 (more varied).",
            implied=0.7,
            minimum=_VOICE_SETTINGS["temp"][0],
            maximum=_VOICE_SETTINGS["temp"][1],
        ),
        Field(
            "cfg_coef",
            "number",
            "How closely the output follows the original voice, from 1 to 4.",
            implied=2.0,
            minimum=_VOICE_SETTINGS["cfg_coef"][0],
            maximum=_VOICE_SETTINGS["cfg_coef"][1],
        ),
        Field(
            "padding_bonus",
            "number",
            "The speaking pace, from -4 to 4: a negative value speaks faster, a "
            "positive one slower.",
            implied=0.0,
            minimum=_VOICE_SETTINGS["padding_bonus"][0],
            maximum=_VOICE_SETTINGS["padding_bonus"][1],
        ),
        Field(
            "rewrite_rules",
            "string",
            "The language whose text-rewriting rules (numbers, dates) are applied "
            "before synthesis, or 'none' to disable them. Left unset, the voice's "
            "language is used.",
            advanced=True,
        ),
        Field(
            "timeout",
            "number",
            "Seconds to wait for the connection to be ready, and for the next "
            "message once the whole text has been sent, before the utterance "
            "fails.",
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
            advanced=True,
        ),
        Field(
            "pronunciation_id",
            "string",
            "The id of a pronunciation dictionary of the account to apply.",
            advanced=True,
        ),
        Field(
            "base_url",
            "string",
            "The API base URL, for a regional or self-hosted endpoint.",
            advanced=True,
        ),
    )

    def __init__(self, config: dict) -> None:
        values = self.validated(config)
        try:
            from gradium import GradiumClient
        except ImportError as exc:
            raise ConfigError(
                "The 'gradium' module requires the gradium extra: "
                "pip install tts-engine[gradium]"
            ) from exc

        api_key = resolve_api_key(config, label=_LABEL)

        # Sent only when set, so the API's own defaults apply otherwise.
        json_config: dict[str, float | str] = {
            field: values[field]
            for field in (*_VOICE_SETTINGS, "rewrite_rules")
            if values[field] is not None
        }

        self._timeout: float = values["timeout"]
        self._voice_id: str = values["voice_id"]
        self._model: str = values["model"]
        self._sample_rate: int = values["sample_rate"]
        self._json_config = json_config
        self._pronunciation_id: str | None = values["pronunciation_id"]

        base_url: str | None = values["base_url"]
        # The key is always passed explicitly: the SDK's own GRADIUM_API_KEY
        # fallback is never relied on, so this module's key rules are the only
        # ones in force.
        if base_url is None:
            self._client = GradiumClient(api_key=api_key)
        else:
            self._client = GradiumClient(api_key=api_key, base_url=base_url)

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The public catalog and the account's own voices, from
        `voice_get(include_catalog=True)`. The SDK is async, so the call runs
        on a private event loop in a helper thread, which also works when the
        caller's thread runs a loop of its own."""
        try:
            from gradium import GradiumClient
        except ImportError as exc:
            raise ConfigError(
                "The 'gradium' module requires the gradium extra: "
                "pip install tts-engine[gradium]"
            ) from exc
        api_key = resolve_api_key(config, label=_LABEL)
        timeout = finite_number(
            config,
            "timeout",
            label=_LABEL,
            default=_DEFAULT_TIMEOUT,
            exclusive_minimum=0,
        )
        base_url = optional_str(config, "base_url", label=_LABEL)
        if base_url is None:
            client = GradiumClient(api_key=api_key)
        else:
            client = GradiumClient(api_key=api_key, base_url=base_url)

        async def fetch() -> Any:
            return await asyncio.wait_for(
                client.voice_get(include_catalog=True), timeout
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                entries = pool.submit(asyncio.run, fetch()).result()
        except Exception as exc:
            raise TTSError(f"{_LABEL}: voice listing failed: {exc!r}") from exc
        if not isinstance(entries, list):
            raise TTSError(
                f"{_LABEL}: voice listing failed: unexpected response {entries!r}"
            )

        voices: list[VoiceInfo] = []
        for entry in entries:
            uid = entry.get("uid")
            if not uid:
                continue
            language = entry.get("locale") or entry.get("language")
            tags = [
                tag.get("value")
                for tag in entry.get("tags") or ()
                if tag.get("category") not in ("gender", "age") and tag.get("value")
            ]
            if not entry.get("is_catalog", True):
                tags.append("custom")
            voices.append(
                VoiceInfo(
                    id=uid,
                    name=entry.get("name") or uid,
                    config={"voice_id": uid},
                    languages=(language,) if language else (),
                    gender=normalize_gender(entry.get("gender")),
                    age=entry.get("age") or None,
                    description=entry.get("description") or None,
                    tags=tuple(tags),
                )
            )
        return voices

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

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

    async def _open(self, stop: threading.Event):
        """Open one realtime connection and check its `ready` message.

        Returns `(context_manager, tts)`, or None when `stop` was set while
        the connection was still being set up (the utterance is aborted, the
        partial connection released). The caller must `_leave` the context
        manager. The setup (socket, setup message, `ready`) is polled against
        `stop` every 100 ms and bounded by `timeout`; a failure, the timeout
        and a `ready` sample-rate mismatch are `TTSError`, and in every
        failing case whatever the SDK had already opened is closed again.
        """
        try:
            tts_cm = self._client.tts_realtime(
                wait_for_ready_on_start=True, **self._setup()
            )
        except Exception as exc:
            raise TTSError(f"Gradium request failed: {exc}") from exc

        # The SDK's `__aenter__` opens the session and socket, sends the setup
        # and waits for `ready` with no timeout of its own, and closes nothing
        # when it fails or is cancelled partway: `_leave` does that here.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        entering = asyncio.ensure_future(tts_cm.__aenter__())
        while not entering.done():
            await asyncio.wait({entering}, timeout=_RECV_POLL_SECONDS)
            if entering.done():
                break
            if stop.is_set() or loop.time() >= deadline:
                entering.cancel()
                await asyncio.wait({entering})
                await self._leave(tts_cm, failed=True)
                if stop.is_set():
                    return None
                raise TTSError(
                    f"Gradium request failed: not ready within {self._timeout} s"
                )
        try:
            tts = entering.result()
        except Exception as exc:
            await self._leave(tts_cm, failed=True)
            raise TTSError(f"Gradium request failed: {exc}") from exc

        ready_rate = (tts.ready or {}).get("sample_rate")
        if ready_rate is not None and ready_rate != self._sample_rate:
            await self._leave(tts_cm, failed=True)
            raise TTSError(
                f"Gradium stream is {ready_rate} Hz but the module "
                f"declares {self._sample_rate} Hz"
            )
        return tts_cm, tts

    @staticmethod
    async def _leave(tts_cm, *, failed: bool) -> None:
        """Close the connection (socket and session at once). After a failure
        a close error is only logged, so the primary error is what the caller
        sees; on a normal end it propagates as `TTSError`."""
        try:
            await tts_cm.__aexit__(None, None, None)
        except Exception as exc:
            if failed:
                log.warning("Gradium connection close failed", exc_info=True)
                return
            raise TTSError(f"Gradium request failed: {exc}") from exc

    async def _receive(
        self,
        tts,
        callback: Callable[[bytes], None],
        stop: threading.Event,
        sender: asyncio.Task | None,
    ) -> None:
        """Feed audio messages to `callback` until the server ends the stream
        or `stop` is set. With a `sender` task, race it against each message so
        a sender failure ends the utterance instead of waiting for audio that
        will never come.

        Once the whole text is with the server (no `sender`, or it has sent
        end-of-stream) the server owes the rest of the audio, so its silence
        is bounded: `timeout` without a message is a `TTSError`. While text is
        still arriving a silence is legitimate (a paused source) and is not."""
        try:
            stream_iter = tts.__aiter__()
        except Exception as exc:
            raise TTSError(f"Gradium request failed: {exc}") from exc

        loop = asyncio.get_running_loop()
        # When the wait for the current message began, once the text is
        # complete. Started after `callback` returns, so the time a blocking
        # sink takes to play a chunk is never counted as the server's silence.
        idle_since: float | None = None
        # The pending receive outlives a poll: waiting on it with a timeout
        # (never cancelling and re-issuing it) is what lets `stop` be honored
        # while the server is silent without losing a message.
        pending: asyncio.Future | None = None
        try:
            while not stop.is_set():
                if idle_since is None and (sender is None or sender.done()):
                    idle_since = loop.time()
                current: asyncio.Future = (
                    pending
                    if pending is not None
                    else asyncio.ensure_future(stream_iter.__anext__())
                )
                pending = current
                waiting: set[asyncio.Future] = {current}
                if sender is not None and not sender.done():
                    waiting.add(sender)
                await asyncio.wait(
                    waiting,
                    timeout=_RECV_POLL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if sender is not None and sender.done() and not sender.cancelled():
                    sender_error = sender.exception()
                    if sender_error is not None:
                        raise sender_error  # a TTSError, see _send_all
                if not current.done():
                    if (
                        idle_since is not None
                        and loop.time() - idle_since >= self._timeout
                    ):
                        raise TTSError(
                            "Gradium request failed: no message for "
                            f"{self._timeout:g} s after the end of the text"
                        )
                    continue  # a poll: re-check `stop`, keep the receive pending
                pending = None
                idle_since = None
                try:
                    msg = current.result()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"Gradium request failed: {exc}") from exc
                if msg.get("type") == "audio":
                    callback(msg["audio"])
        finally:
            if pending is not None and not pending.done():
                # Aborted: no end-of-stream was sent, so no final audio will
                # come; don't wait for it.
                pending.cancel()

    async def _consume(
        self, text: str, callback: Callable[[bytes], None], stop: threading.Event
    ) -> None:
        """Run one complete-text utterance over one WebSocket.

        Runs on the worker thread's private loop. SDK/connection failures
        become TTSError; `callback` is invoked outside that boundary so a
        downstream playback failure keeps its own identity.
        """
        opened = await self._open(stop)
        if opened is None:
            return  # cancelled during setup
        tts_cm, tts = opened
        try:
            try:
                await tts.send_text(text)
                await tts.send_eos()
            except Exception as exc:
                raise TTSError(f"Gradium request failed: {exc}") from exc
            await self._receive(tts, callback, stop, sender=None)
        except BaseException:
            # Closes the socket and session at once, so a cancel mid-utterance
            # drops the request instead of draining the rest of the audio.
            await self._leave(tts_cm, failed=True)
            raise
        await self._leave(tts_cm, failed=False)

    @staticmethod
    async def _send_all(tts, pieces: Iterator[str], stop: threading.Event) -> None:
        """Forward the text pieces to the socket as they arrive, grouped at
        word boundaries, then end-of-stream. `next(pieces)` blocks waiting for
        text, so it runs in an executor to keep the receiver going."""
        loop = asyncio.get_running_loop()
        buffer = ""
        try:
            while True:
                piece = await loop.run_in_executor(None, next, pieces, None)
                if piece is None:
                    break
                buffer += piece
                words, buffer = split_at_last_space(buffer)
                if words:
                    await tts.send_text(words)
            if stop.is_set():
                return  # aborted: no end-of-stream, the request is dropped
            if buffer:
                await tts.send_text(buffer)
            await tts.send_eos()
        except Exception as exc:
            raise TTSError(f"Gradium request failed: {exc}") from exc

    async def _consume_stream(
        self,
        pieces: Iterator[str],
        callback: Callable[[bytes], None],
        stop: threading.Event,
    ) -> None:
        """Run one text-stream utterance over one WebSocket: a sender task
        forwarding text concurrently with the receiver feeding audio."""
        opened = await self._open(stop)
        if opened is None:
            return  # cancelled during setup
        tts_cm, tts = opened
        sender = asyncio.create_task(self._send_all(tts, pieces, stop))
        failed = True
        try:
            await self._receive(tts, callback, stop, sender)
            failed = False
        finally:
            # Release a sender still blocked on text (`stop` ends `pieces`) so
            # its executor thread exits before this loop is torn down, then
            # drop the request.
            stop.set()
            if not sender.done():
                sender.cancel()
            await asyncio.wait([sender])
            await self._leave(tts_cm, failed=failed)

    async def synthesize_text(
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

    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _blocking_stream(pieces: Iterator[str], stop: threading.Event) -> None:
            asyncio.run(self._consume_stream(pieces, callback, stop))

        await run_text_stream_worker(text_stream, _blocking_stream)
