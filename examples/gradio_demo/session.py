"""The demo's sessions, with no gradio (specs/gradio-demo.md).

A `SpeechSession` is a running engine. The page's handlers run on gradio's worker threads;
the engine's calls are coroutines. The session owns one event loop on a thread of its own
and runs every call there, so a handler just waits for the result. One utterance at a time:
a new one waits for the previous.

`Voice` is what the page's Speak uses: the session built from the app's saved config when an
utterance needs it, rebuilt when that config has changed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app_setup.app import AppModel

from tts_engine.audio import AudioSink
from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine
from tts_engine.utterance import UtteranceResult

__all__ = ["SpeechSession", "Voice", "build_session", "describe_result"]


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


class Voice:
    """The engine the page speaks with, built from the app's saved config when it is
    needed: nothing runs between utterances, so nothing is started or locked.

    Each `say` reads the saved config and reuses the engine built from the same `tts`
    section, else closes it and builds one from the new section; a save (from any browser
    tab, or the file edited by hand) is heard at the next utterance. One utterance at a time:
    a second waits, and an engine is only replaced between two.
    """

    def __init__(self) -> None:
        self._speaking = threading.Lock()
        self._section: dict[str, Any] | None = None
        self.session: SpeechSession | None = None

    def say(self, model: AppModel, text: str) -> UtteranceResult | list[str]:
        """Speak *text* with the saved config's voice; why it cannot, as a list, instead.
        Raises what the engine raises; `concurrent.futures.CancelledError` when
        interrupted."""
        with self._speaking:
            config = model.saved_config()
            blockers = model.blockers(config)
            if blockers:
                return blockers
            section = model.used_config(config)["tts"]
            if self.session is None or section != self._section:
                self._close()
                try:
                    self.session = build_session(section, model.path)
                except Exception as exc:  # noqa: BLE001 - said on the page
                    return [
                        f"The voice could not be built: {str(exc) or type(exc).__name__}"
                    ]
                self._section = section
            return self.session.say(text)

    def interrupt(self) -> bool:
        """Cancel the utterance in progress; False when nothing is being said."""
        session = self.session
        return session is not None and session.interrupt()

    def close(self) -> None:
        """Close the engine, if one was built: when the page's process ends."""
        self.interrupt()
        with self._speaking:
            self._close()

    def _close(self) -> None:
        session, self.session, self._section = self.session, None, None
        if session is not None:
            session.stop()
