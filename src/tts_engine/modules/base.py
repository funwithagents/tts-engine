"""TTSModule ABC, TTSOptions, TTSError, and the helpers every backend shares."""

import asyncio
import queue
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


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
    async def synthesize_text(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize the complete `text` and call `callback` with each PCM chunk
        as it arrives.

        - `text`: the string to synthesize. Must not be empty.
        - `options`: per-call overrides; currently empty and reserved for future use.
        - `callback`: called sequentially with each raw audio chunk. Calls may run
          off the event-loop thread but must never overlap.

        Raises `TTSError` on synthesis failure.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """

    @abstractmethod
    async def synthesize_text_stream(
        self,
        text_stream: AsyncIterator[str],
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize text as it arrives from `text_stream` and call `callback`
        with each PCM chunk as it is produced: audio for the first part of the
        text should play while later parts are still being received.

        - `text_stream`: an async iterator of text pieces, concatenated verbatim
          (no separator is inserted; an LLM token stream carries its own
          spaces). Empty pieces are skipped. An exhausted stream with no text
          produces no audio and is not an error.
        - `options`, `callback`: as for `synthesize_text`.

        Raises `TTSError` on synthesis failure. An exception raised by
        `text_stream` itself aborts the utterance and propagates unchanged.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """


async def run_cancellable_worker(
    worker: Callable[[threading.Event], None],
    *,
    stop: threading.Event | None = None,
) -> None:
    """Run a blocking ``worker`` in a thread with cooperative cancellation.

    ``worker(stop)`` is executed via ``asyncio.to_thread``. It must poll
    ``stop.is_set()`` between chunks and return promptly once it is set.

    If the awaiting coroutine is cancelled, this sets ``stop``, waits for the
    thread to actually exit, and only then re-raises ``CancelledError``, so a
    module's synthesis never returns or raises while its worker can still
    invoke ``callback``. Any exception the worker raises while unwinding after
    a cancel is discarded (the caller's outcome is the cancellation). Without
    a cancel, the worker's exception propagates unchanged.

    ``stop`` may be supplied by a caller that needs to set it from elsewhere
    (``run_text_stream_worker`` sets it when the text source fails); by default
    a fresh event is created.
    """
    if stop is None:
        stop = threading.Event()
    task = asyncio.ensure_future(asyncio.to_thread(worker, stop))
    try:
        # shield: cancelling *us* must not cancel the executor future; we
        # still need to await it below so the thread has really stopped.
        await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await asyncio.wait([task])
        if not task.cancelled():
            task.exception()  # mark retrieved so asyncio doesn't log it at GC
        raise


_END = object()
_POLL_SECONDS = 0.05


async def run_text_stream_worker(
    text_stream: AsyncIterator[str],
    worker: Callable[[Iterator[str], threading.Event], None],
) -> None:
    """Run a blocking ``worker(pieces, stop)`` in a thread, fed from an async
    text stream, with cooperative cancellation (see ``run_cancellable_worker``).

    ``pieces`` is a blocking iterator over the stream's text pieces: ``next``
    waits for the next piece, ends at end-of-stream, and ends early when
    ``stop`` is set (so a worker blocked on text still exits promptly). Empty
    pieces are dropped before they reach the worker.

    A pump task on the calling loop consumes ``text_stream`` into a queue. If
    the stream raises, the utterance is aborted: ``stop`` is set, and once the
    worker thread has exited the stream's exception is raised unchanged (it is
    the caller's error, never a ``TTSError``). If the worker fails or returns
    before the stream is exhausted, the pump is cancelled so the stream is
    closed early rather than consumed to the end.
    """
    stop = threading.Event()
    q: queue.Queue[object] = queue.Queue()
    source_error: Exception | None = None

    async def pump() -> None:
        nonlocal source_error
        try:
            async for piece in text_stream:
                if piece:
                    q.put(piece)
        except Exception as exc:  # noqa: BLE001  (re-raised once the thread exits)
            source_error = exc
            stop.set()
        finally:
            q.put(_END)

    def pieces() -> Iterator[str]:
        while not stop.is_set():
            try:
                item = q.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue
            if item is _END:
                return
            yield item  # type: ignore[misc]

    pump_task = asyncio.create_task(pump())
    try:
        await run_cancellable_worker(lambda s: worker(pieces(), s), stop=stop)
    except asyncio.CancelledError:
        pump_task.cancel()
        await asyncio.wait([pump_task])
        raise
    except Exception:
        pump_task.cancel()
        await asyncio.wait([pump_task])
        if source_error is not None:
            # The worker failed because the source aborted it: the source's
            # error is the one the caller needs to see.
            raise source_error
        raise
    pump_task.cancel()  # a no-op when the stream was consumed to the end
    await asyncio.wait([pump_task])
    if source_error is not None:
        raise source_error


# A sentence end: terminal punctuation, optional closing quotes/brackets, then
# whitespace (so "3.14" and "v2.5" stay whole); or a newline on its own.
_SENTENCE_END = re.compile(r"[.!?…][\"')\]»”’]*\s|\n")
_CLAUSE_END = re.compile(r"[,;:]\s")
_WHITESPACE = re.compile(r"\s+")
_DEFAULT_MAX_CHARS = 200


def segment_sentences(
    pieces: Iterable[str], *, max_chars: int = _DEFAULT_MAX_CHARS
) -> Iterator[str]:
    """Group text pieces into sentences, yielding each as soon as it is complete.

    Pieces are concatenated verbatim. A sentence is yielded (stripped) once the
    buffer holds terminal punctuation followed by whitespace, or a newline.
    Past ``max_chars`` with no sentence end, the buffer is cut at the last
    clause boundary (``,`` ``;`` ``:`` + whitespace), else at the last
    whitespace, never inside a word. The remainder is yielded at end of input.
    Blank segments are never yielded.
    """
    buffer = ""
    for piece in pieces:
        if not piece:
            continue
        buffer += piece
        while True:
            match = _SENTENCE_END.search(buffer)
            if match is not None:
                cut = match.end()
            elif len(buffer) > max_chars:
                cut = _forced_cut(buffer)
                if cut is None:
                    break
            else:
                break
            segment, buffer = buffer[:cut].strip(), buffer[cut:]
            if segment:
                yield segment
    tail = buffer.strip()
    if tail:
        yield tail


def _forced_cut(buffer: str) -> int | None:
    """Where to cut an over-long buffer: after the last clause boundary, else
    after the last whitespace run; None when there is no whitespace at all."""
    last = None
    for match in _CLAUSE_END.finditer(buffer):
        last = match.end()
    if last is not None:
        return last
    for match in _WHITESPACE.finditer(buffer):
        last = match.end()
    return last


def split_at_last_space(buffer: str) -> tuple[str, str]:
    """Split `buffer` after its last whitespace: (complete words, the rest).

    For backends that take text incrementally: forwarding only up to the last
    whitespace holds a possibly unfinished last word back, so a sub-word token
    never reaches the server on its own, whichever side the tokenizer puts its
    spaces on. The rest is sent with the next piece or at end of stream.
    """
    for i in range(len(buffer) - 1, -1, -1):
        if buffer[i].isspace():
            return buffer[: i + 1], buffer[i + 1 :]
    return "", buffer


def resolve_path(config: dict, value: str) -> Path:
    """Resolve a file path from a module config field.

    Absolute paths are returned as-is. A relative path is joined onto
    ``config["base_dir"]`` when present, else onto the current working
    directory. This is a locate only: the file is not opened or checked.
    """
    path = Path(value)
    if path.is_absolute():
        return path
    base_dir = config.get("base_dir")
    if base_dir:
        return Path(base_dir) / path
    return Path.cwd() / path
