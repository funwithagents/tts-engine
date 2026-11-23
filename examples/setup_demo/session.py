"""The demo's sessions, with no gradio (specs/setup-demo.md).

A `SpeechSession` is a running engine. The page's handlers run on gradio's worker threads;
the engine's calls are coroutines. The session owns one event loop on a thread of its own
and runs every call there, so a handler just waits for the result. One utterance at a time:
a new one waits for the previous.

`Sessions` holds at most one, started from the app's saved config: what the page's Start and
Stop do, and what locks the config while a session runs.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from module_setup.app import AppModel

from tts_engine.audio import AudioSink
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine
from tts_engine.utterance import UtteranceResult

__all__ = ["Sessions", "SpeechSession", "build_session", "describe_result"]


class SpeechSession:
    """A `TTSEngine` built from one engine block, on its own event loop."""

    def __init__(
        self, config: TTSEngineConfig, *, sink: AudioSink | None = None
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="tts-demo-loop", daemon=True
        )
        self._thread.start()
        self._current: concurrent.futures.Future[Any] | None = None
        self._lock = threading.Lock()
        try:
            self._engine = self._run(self._build(config, sink))
        except BaseException:
            self._shutdown_loop()
            raise

    @staticmethod
    async def _build(config: TTSEngineConfig, sink: AudioSink | None) -> TTSEngine:
        # Built on the loop that will run it: the engine's lock belongs to that loop.
        return TTSEngine(config, sink=sink)

    def _run(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    @property
    def sample_rate(self) -> int:
        return self._engine.sample_rate

    def say(self, text: str) -> UtteranceResult:
        """Speak *text* and return once it has been played. Raises what the engine raises;
        `concurrent.futures.CancelledError` when interrupted."""
        future = asyncio.run_coroutine_threadsafe(self._engine.say(text), self._loop)
        with self._lock:
            self._current = future
        try:
            return future.result()
        finally:
            with self._lock:
                if self._current is future:
                    self._current = None

    def interrupt(self) -> bool:
        """Cancel the utterance in progress; False when nothing is being said."""
        with self._lock:
            future = self._current
        if future is None or future.done():
            return False
        return future.cancel()

    def stop(self) -> None:
        """Interrupt, close the engine (its model and device), and end the loop."""
        self.interrupt()
        try:
            self._run(self._engine.close())
        finally:
            self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
        if not self._loop.is_running():
            self._loop.close()


def describe_result(result: UtteranceResult) -> str:
    """One line for the page: how much was said, and how long the listener waited."""
    timing = result.timing
    parts = [f"{result.text_chars} characters, {result.audio_seconds:.2f} s of audio"]
    if timing.first_audio_at is not None:
        wait = (timing.first_audio_at - timing.started_at).total_seconds()
        parts.append(f"first audio after {wait * 1000:.0f} ms")
    if timing.playback_started_at is not None:
        heard = (timing.playback_started_at - timing.started_at).total_seconds()
        parts.append(f"playback after {heard * 1000:.0f} ms")
    return "; ".join(parts) + "."


def build_session(section: Mapping[str, Any], folder: Path) -> SpeechSession:
    """The session for the saved `tts` section: the engine built from it, a relative path
    in it (a voice file) resolved against the app's folder."""
    return SpeechSession(TTSEngineConfig.from_dict(dict(section), base_dir=folder))


class Sessions:
    """At most one `SpeechSession`, started from the app's saved config."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._starting = False
        self._stopping = False
        self.session: SpeechSession | None = None
        self.error: str | None = None  # why the last start failed

    def busy(self) -> bool:
        """Whether a session is running or starting: the config is in use."""
        return self._starting or self.session is not None

    def start(self, model: AppModel) -> list[str]:
        """Start a session with the saved config; why it did not start, else []."""
        with self._lock:
            if self.busy():
                return ["A session is already running."]
            self._starting = True
        try:
            config = model.saved_config()
            blockers = model.blockers(config)
            if blockers:
                return blockers
            try:
                session = build_session(model.used_config(config)["tts"], model.path)
            except Exception as exc:  # noqa: BLE001 - said on the page
                self.error = str(exc) or type(exc).__name__
                return [f"The engine did not start: {self.error}"]
            self.session, self.error = session, None
            return []
        finally:
            self._starting = False

    def stop(self) -> bool:
        """Stop the running session; False when there is none (or it is already stopping).
        The config stays locked until the engine has closed."""
        with self._lock:
            session = self.session
            if session is None or self._stopping:
                return False
            self._stopping = True
        try:
            session.stop()
        finally:
            with self._lock:
                self.session, self._stopping = None, False
        return True
