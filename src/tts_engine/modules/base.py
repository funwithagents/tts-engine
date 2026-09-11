"""TTSModule ABC, TTSOptions, TTSError."""

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


class TTSError(Exception):
    pass


@dataclass
class TTSOptions:
    pass


class TTSModule(ABC):
    def __init__(self, config: dict) -> None:
        """Construct the module from its `engine.module` block (the full dict,
        including `type`). Subclasses validate and extract the fields they
        need, raising `ConfigError` for missing/invalid ones.
        """

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """The sample rate (Hz) of the PCM the module feeds to `callback`.

        Declared by the module and fixed for its lifetime: the engine reads it
        once and opens the `AudioPlayer` output stream at this rate. A module
        must resample internally if its backend's native rate differs.
        """

    @abstractmethod
    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize `text` and call `callback` with each PCM chunk as it arrives.

        - `text`: the string to synthesize. Must not be empty.
        - `options`: per-call overrides; currently empty and reserved for future use.
        - `callback`: called sequentially with each raw audio chunk. Calls may run
          off the event-loop thread but must never overlap.

        Raises `TTSError` on synthesis failure.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """


async def run_cancellable_worker(worker: Callable[[threading.Event], None]) -> None:
    """Run a blocking ``worker`` in a thread with cooperative cancellation.

    ``worker(stop)`` is executed via ``asyncio.to_thread``. It must poll
    ``stop.is_set()`` between chunks and return promptly once it is set.

    If the awaiting coroutine is cancelled, this sets ``stop``, waits for the
    thread to actually exit, and only then re-raises ``CancelledError`` — so a
    module's ``stream()`` never returns or raises while its worker can still
    invoke ``callback``. Any exception the worker raises while unwinding after
    a cancel is discarded (the caller's outcome is the cancellation). Without
    a cancel, the worker's exception propagates unchanged.
    """
    stop = threading.Event()
    task = asyncio.ensure_future(asyncio.to_thread(worker, stop))
    try:
        # shield: cancelling *us* must not cancel the executor future — we
        # still need to await it below so the thread has really stopped.
        await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await asyncio.wait([task])
        if not task.cancelled():
            task.exception()  # mark retrieved so asyncio doesn't log it at GC
        raise
