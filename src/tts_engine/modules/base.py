"""TTSModule ABC, TTSOptions, TTSError, and the helpers every backend shares."""

import asyncio
import contextlib
import logging
import queue
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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
    thread to actually exit — through any further cancellation that arrives
    meanwhile — and only then re-raises ``CancelledError``, so a module's
    synthesis never returns or raises while its worker can still invoke
    ``callback``. Any exception the worker raises while unwinding after a
    cancel is discarded (the caller's outcome is the cancellation). Without a
    cancel, the worker's exception propagates unchanged.

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
        try:
            await _join(task)
        finally:
            if not task.cancelled():
                task.exception()  # mark retrieved so asyncio doesn't log it at GC
        raise


async def _join(task: asyncio.Future[Any]) -> None:
    """Wait for ``task`` to finish even if the awaiting coroutine is cancelled
    (again) meanwhile. A cancel arriving during the wait is remembered and
    re-raised once ``task`` is done, so the cleanup wait for a worker thread
    can never be cut short by a repeated cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


_END = object()
_POLL_SECONDS = 0.05
# The text bridge holds at most this many characters the worker has not taken
# yet; past it the pump waits (on the loop, not blocking it) for the worker to
# catch up, so a fast source cannot run arbitrarily far ahead of playback. One
# piece larger than the bound still goes through once the queue is empty.
MAX_PENDING_CHARS = 4096


async def run_text_stream_worker(
    text_stream: AsyncIterator[str],
    worker: Callable[[Iterator[str], threading.Event], None],
    *,
    max_pending_chars: int = MAX_PENDING_CHARS,
) -> None:
    """Run a blocking ``worker(pieces, stop)`` in a thread, fed from an async
    text stream, with cooperative cancellation (see ``run_cancellable_worker``).

    ``pieces`` is a blocking iterator over the stream's text pieces: ``next``
    waits for the next piece, ends at end-of-stream, and ends early when
    ``stop`` is set (so a worker blocked on text still exits promptly). Empty
    pieces are dropped before they reach the worker.

    A pump task on the calling loop consumes ``text_stream`` into a bounded
    queue: once ``max_pending_chars`` characters are queued and not yet taken
    by the worker, the pump awaits room (yielding the loop) instead of reading
    further, so the source sees backpressure and an immediately yielding
    source cannot starve the loop. If the stream raises — a ``CancelledError``
    of its own included — the utterance is aborted: ``stop`` is set, and once
    the worker thread has exited the stream's exception is raised unchanged
    (it is the caller's error, never a ``TTSError``). If the worker fails or
    returns before the stream is exhausted, the pump is cancelled so the
    stream is closed early rather than consumed to the end.
    """
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    q: queue.Queue[object] = queue.Queue()
    source_error: BaseException | None = None
    pending = 0  # characters queued and not yet taken; loop thread only
    room = asyncio.Event()  # set (from the worker thread) when a piece is taken

    def taken(n: int) -> None:
        nonlocal pending
        pending -= n
        room.set()

    async def pump() -> None:
        nonlocal source_error, pending
        try:
            async for piece in text_stream:
                if not piece:
                    # Give the loop a turn: a source yielding only empty
                    # pieces without suspending would otherwise spin here.
                    await asyncio.sleep(0)
                    continue
                while pending and pending + len(piece) > max_pending_chars:
                    room.clear()
                    await room.wait()
                pending += len(piece)
                q.put(piece)
        except asyncio.CancelledError as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # the bridge's own cancel: worker done, or caller cancelled
            # The source raised CancelledError of its own accord: a source
            # failure like any other, not a successful end of stream.
            source_error = exc
            stop.set()
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
            assert isinstance(item, str)
            loop.call_soon_threadsafe(taken, len(item))
            yield item

    pump_task = asyncio.create_task(pump())
    try:
        await run_cancellable_worker(lambda s: worker(pieces(), s), stop=stop)
    except asyncio.CancelledError:
        pump_task.cancel()
        await _join(pump_task)
        raise
    except Exception:
        pump_task.cancel()
        await _join(pump_task)
        if source_error is not None:
            # The worker failed because the source aborted it: the source's
            # error is the one the caller needs to see.
            raise source_error
        raise
    pump_task.cancel()  # a no-op when the stream was consumed to the end
    await _join(pump_task)
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
    buffer holds terminal punctuation followed by whitespace, or a newline,
    within its first ``max_chars`` characters. Once the buffer is longer than
    ``max_chars`` with no sentence end in that window, it is cut inside the
    window at the last clause boundary (``,`` ``;`` ``:`` + whitespace), else
    at the last whitespace, never inside a word; a single word longer than
    ``max_chars`` is the one segment allowed past the bound (cut at the first
    whitespace after it). So no segment exceeds ``max_chars`` except such a
    word, however the input is chunked. The remainder is yielded at end of
    input. Blank segments are never yielded.
    """
    buffer = ""
    for piece in pieces:
        if not piece:
            continue
        buffer += piece
        while True:
            match = _SENTENCE_END.search(buffer)
            # The stripped segment is one shorter than the match (its trailing
            # whitespace goes), so a match ending at max_chars + 1 still fits.
            if match is not None and match.end() <= max_chars + 1:
                cut = match.end()
            elif len(buffer) > max_chars:
                cut = _forced_cut(buffer, max_chars)
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


def _forced_cut(buffer: str, max_chars: int) -> int | None:
    """Where to cut a buffer longer than ``max_chars``: after the last clause
    boundary within the first ``max_chars`` characters, else after the last
    whitespace run there, else (a word longer than ``max_chars``) after the
    first whitespace beyond it; None when there is no whitespace at all."""
    head = buffer[: max_chars + 1]
    last = None
    for match in _CLAUSE_END.finditer(head):
        last = match.end()
    if last is not None:
        return last
    for match in _WHITESPACE.finditer(head):
        last = match.end()
    if last is not None:
        return last
    match = _WHITESPACE.search(buffer, max_chars + 1)
    return match.end() if match is not None else None


def feed_with_lookahead(
    sentences: Iterable[str],
    synthesize: Callable[[str], Iterable[bytes]],
    callback: Callable[[bytes], None],
    stop: threading.Event,
    *,
    slice_bytes: int,
) -> None:
    """Synthesize ``sentences`` one step ahead of playback and feed the PCM.

    For runtimes that return a whole sentence of audio at once. Called from a
    module's worker thread: a producer thread iterates ``sentences`` and
    ``synthesize(sentence)``, handing each PCM chunk over a queue at most one
    chunk ahead: the next is synthesized while this thread feeds the current
    one to ``callback`` in slices of at most ``slice_bytes``, checking
    ``stop`` between slices. ``callback`` is only called from this thread.

    An exception from ``sentences`` or ``synthesize`` is raised here
    unchanged. The producer is not joined: a running inference call usually
    cannot be interrupted, so it finishes in the background, then sees
    ``stop`` (or this call's end) and exits without feeding anything.
    """
    chunks: queue.Queue[object] = queue.Queue()
    # One slot: the producer takes it before synthesizing a chunk, the feeder
    # gives it back when it starts playing one, so at most one chunk is ever
    # synthesized ahead of the chunk being played.
    slot = threading.Semaphore(1)
    done = threading.Event()

    def take_slot() -> bool:
        while not (stop.is_set() or done.is_set()):
            if slot.acquire(timeout=_POLL_SECONDS):
                return True
        return False

    def produce() -> None:
        last: object = _END
        try:
            for sentence in sentences:
                if not take_slot():
                    return
                pcm_chunks = iter(synthesize(sentence))
                while True:
                    try:
                        pcm = next(pcm_chunks)
                    except StopIteration:
                        slot.release()  # held for a chunk that never came
                        break
                    chunks.put(pcm)
                    if not take_slot():
                        return
        except Exception as exc:  # noqa: BLE001  (raised by the caller)
            last = exc
        chunks.put(last)

    threading.Thread(target=produce, name="tts-lookahead", daemon=True).start()
    try:
        while not stop.is_set():
            try:
                item = chunks.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, bytes)
            slot.release()  # playing this one: the next may be synthesized
            for start in range(0, len(item), slice_bytes):
                if stop.is_set():
                    return
                callback(item[start : start + slice_bytes])
    finally:
        done.set()


@contextlib.contextmanager
def cleanup(close: Callable[[], object], what: str) -> Iterator[None]:
    """Run ``close()`` when the block ends, without letting it hide the block's
    own outcome.

    If the block raised (a ``TTSError``, a callback failure, a
    ``CancelledError``), that exception propagates unchanged and a failure of
    ``close`` is logged at WARNING level, so the caller sees the primary
    error. If the block completed, ``close`` runs normally and its failure
    propagates: with no primary error it is the only thing that went wrong.
    """
    try:
        yield
    except BaseException:
        try:
            close()
        except Exception:  # the block's exception is the one to report
            log.warning("%s: cleanup failed after an error", what, exc_info=True)
        raise
    else:
        close()


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
