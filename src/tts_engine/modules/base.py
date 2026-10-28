"""TTSModule ABC, TTSOptions, TTSError, and the helpers every backend shares."""

import asyncio
import contextlib
import logging
import math
import os
import queue
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar, cast, overload

import numpy as np

from tts_engine.config import ConfigError

log = logging.getLogger(__name__)


class TTSError(Exception):
    pass


@dataclass
class TTSOptions:
    pass


Gender = Literal["male", "female", "neutral"]

_GENDER_WORDS: dict[str, Gender] = {
    "male": "male",
    "masculine": "male",
    "m": "male",
    "man": "male",
    "female": "female",
    "feminine": "female",
    "f": "female",
    "woman": "female",
    "neutral": "neutral",
    "gender_neutral": "neutral",
    "gender-neutral": "neutral",
    "non-binary": "neutral",
    "nonbinary": "neutral",
    "non_binary": "neutral",
    "androgynous": "neutral",
}


def normalize_gender(value: object) -> Gender | None:
    """A backend's gender wording as a `Gender`, or None when it is not one."""
    if not isinstance(value, str):
        return None
    return _GENDER_WORDS.get(value.strip().lower())


@dataclass(frozen=True)
class VoiceInfo:
    """One voice of a backend's catalog (see `TTSModule.list_voices`)."""

    id: str
    name: str
    # The `engine.module` fields that select this voice, to merge into a config.
    config: Mapping[str, Any]
    languages: tuple[str, ...] = ()
    gender: Gender | None = None
    # The backend's own wording ("young", "Adult", "30"): the scales differ.
    age: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()


class VoiceListingUnsupported(Exception):
    """The backend has no catalog the module can read; the message says where
    its voices are documented."""


# Where a module's synthesis runs: on a provider's servers (`hosted`), in this
# process (`local`), or nowhere — deterministic audio for tests (`fixture`).
ModuleKind = Literal["hosted", "local", "fixture"]


class TTSModule(ABC):
    # Declared by every module (a class attribute, no default): what an
    # embedder needs to know before trying it — does it need a key and a
    # network, or weights on disk — and how the benchmark groups its results.
    kind: ClassVar[ModuleKind]

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

    def close(self) -> None:
        """Release what the module holds for its lifetime: a provider closes
        its SDK client, a local model drops its weights. Called once, by
        `TTSEngine.close()`, with no utterance in flight; the module must not
        be used afterwards. Threads that outlive an utterance (a socket's
        closing tail, a lookahead producer, an abandoned connection setup)
        hold nothing of the module's and are not waited for. The default
        releases nothing: a fixture module has nothing to release.
        """

    @classmethod
    def list_voices(cls, config: dict) -> list[VoiceInfo]:
        """The voices this backend offers, read from its own catalog.

        Takes the `engine.module` block but reads only what listing needs
        (credentials, endpoint, the fields that change which voices exist), so
        a config without a voice is enough; constructs no module. Blocking:
        it does network or file I/O on the calling thread. Raises `ConfigError`
        for a missing extra or an invalid field, `TTSError` when the provider
        or a download fails, and `VoiceListingUnsupported` when the backend
        has no catalog to read. Every module overrides this default.
        """
        raise VoiceListingUnsupported(
            f"The {config.get('type', cls.__name__)!r} module cannot list its voices"
        )


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


_T = TypeVar("_T")


def open_cancellable(
    enter: Callable[[], _T],
    leave: Callable[[_T], object],
    *,
    stop: threading.Event,
    timeout: float,
    what: str,
) -> _T | None:
    """Run a blocking setup step (``enter``: a socket's handshake) on a helper
    thread and wait for it while polling ``stop``.

    Returns what ``enter`` returned, or ``None`` once ``stop`` is set while it
    was still running (the utterance is being abandoned). Raises
    ``TimeoutError`` once ``timeout`` seconds have passed, and ``enter``'s own
    exception unchanged when it fails. A result that arrives after the wait
    gave up is handed to ``leave`` on the helper thread, so an abandoned
    connection is closed rather than leaked; a failure of that late ``leave``
    is logged. The helper thread is a daemon and is not joined.
    """
    outcome: list[object] = []
    ready = threading.Event()
    lock = threading.Lock()
    gave_up = False

    def run() -> None:
        try:
            value = enter()
        except BaseException as exc:  # noqa: BLE001  (re-raised by the waiter)
            with lock:
                outcome.append(exc)
                ready.set()
            return
        with lock:
            if not gave_up:
                outcome.append(value)
                ready.set()
                return
        try:
            leave(value)
        except Exception:
            log.debug("%s: closing an abandoned connection failed", what, exc_info=True)

    threading.Thread(target=run, name=f"{what}-open", daemon=True).start()
    deadline = time.monotonic() + timeout
    while not ready.wait(_POLL_SECONDS):
        if stop.is_set() or time.monotonic() >= deadline:
            with lock:
                if not ready.is_set():
                    gave_up = True
                    if stop.is_set():
                        return None
                    raise TimeoutError(f"{what}: not open within {timeout:g} s")
    (value,) = outcome
    if isinstance(value, BaseException):
        raise value
    return cast(_T, value)


# A sentence end for `cut_at_boundary`: terminal punctuation, optional closing
# quotes/brackets, then whitespace (kept with the cut piece) or a newline.
_BOUNDARY_SENTENCE_END = re.compile(r"(?:[.!?…][\"'”’)\]»]*\s|\n)")


def cut_at_boundary(text: str, room: int) -> int:
    """Where to cut ``text`` so the first part fits in ``room`` characters:
    after the last sentence end within it, else after the last whitespace,
    else at ``room`` (a single word longer than the room). The cut keeps the
    text verbatim: the two parts concatenate back to ``text``."""
    head = text[:room]
    ends = [m.end() for m in _BOUNDARY_SENTENCE_END.finditer(head)]
    if ends:
        return ends[-1]
    space = max(head.rfind(" "), head.rfind("\t"))
    return space + 1 if space > 0 else room


def slice_text(text: str, max_chars: int) -> Iterator[str]:
    """Yield ``text`` verbatim in slices of at most ``max_chars`` characters,
    each cut with ``cut_at_boundary`` (a word longer than that is the one
    slice allowed past the bound). For a backend that takes text
    incrementally: a complete text sent in slices gives an admission gate
    its grain, the way an LLM's pieces do for a stream."""
    while text:
        if len(text) <= max_chars:
            yield text
            return
        cut = cut_at_boundary(text, max_chars)
        yield text[:cut]
        text = text[cut:]


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


# --- Shared config helpers ----------------------------------------------------
#
# Every module validates its own `engine.module` block, but the fields most of
# them share (`api_key`/`api_key_env`, optional strings, bounded numbers such
# as `timeout`) are checked here once, so a fix to one check reaches every
# backend. `label` ("ElevenLabs module") prefixes each message.


def resolve_api_key(config: dict, *, label: str) -> str:
    """Resolve a provider's API key from a literal ``api_key`` or, failing
    that, the environment variable named by ``api_key_env``, so a config file
    can be committed with only the variable's name and no secret.

    A non-empty literal wins. Otherwise the variable named by ``api_key_env``
    is read, and its being unset or empty is a ``ConfigError`` naming it. A
    non-string value for either field, or neither yielding a key, is a
    ``ConfigError`` too.
    """
    api_key = config.get("api_key")
    env_name = config.get("api_key_env")
    if api_key is not None and not isinstance(api_key, str):
        raise ConfigError(f"{label} 'api_key' must be a string")
    if env_name is not None and not isinstance(env_name, str):
        raise ConfigError(f"{label} 'api_key_env' must be a string")

    if api_key:
        return api_key

    if env_name:
        api_key = os.environ.get(env_name)
        if not api_key:
            raise ConfigError(
                f"{label}: environment variable {env_name!r} "
                "(named by 'api_key_env') is unset or empty"
            )
        return api_key

    raise ConfigError(f"{label} requires a non-empty 'api_key' or 'api_key_env'")


@overload
def optional_str(config: dict, field: str, *, label: str) -> str | None: ...


@overload
def optional_str(config: dict, field: str, *, label: str, default: str) -> str: ...


def optional_str(
    config: dict, field: str, *, label: str, default: str | None = None
) -> str | None:
    """Read a string field that may be absent.

    An absent field gives ``default`` (``None`` without one). A field that is
    present must be a non-empty string, else ``ConfigError``; the one
    exception is an explicit ``null`` with no ``default``, which counts as
    absent. With a ``default`` an explicit ``null`` is rejected like any other
    non-string: a value the config names is validated, only a missing one is
    defaulted.
    """
    value = config.get(field, default)
    if value is None and default is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} '{field}' must be a non-empty string")
    return value


@overload
def finite_number(
    config: dict,
    field: str,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float | None: ...


@overload
def finite_number(
    config: dict,
    field: str,
    *,
    label: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float: ...


def finite_number(
    config: dict,
    field: str,
    *,
    label: str,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float | None:
    """Read a numeric field that may be absent, as a float.

    The absent/``null``/``default`` policy is ``optional_str``'s. A present
    value must be an int or float (a bool is rejected rather than treated as
    an integer) that is finite (``NaN`` and the infinities, which JSON's
    parser accepts, would reach a sampler or a timeout) and within the bounds:
    ``minimum`` and ``maximum`` inclusive, ``exclusive_minimum`` exclusive.
    The message names the field and the bounds (``"... must be a finite
    number > 0"``).
    """
    value = config.get(field, default)
    if value is None and default is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
        or (exclusive_minimum is not None and value <= exclusive_minimum)
    ):
        raise ConfigError(
            f"{label} '{field}' must be "
            + _describe_bounds(minimum, maximum, exclusive_minimum)
        )
    return float(value)


def _describe_bounds(
    minimum: float | None, maximum: float | None, exclusive_minimum: float | None
) -> str:
    if minimum is not None and maximum is not None:
        return f"a finite number between {minimum:g} and {maximum:g}"
    bounds = []
    if exclusive_minimum is not None:
        bounds.append(f"> {exclusive_minimum:g}")
    if minimum is not None:
        bounds.append(f">= {minimum:g}")
    if maximum is not None:
        bounds.append(f"<= {maximum:g}")
    return "a finite number" + (" " + " and ".join(bounds) if bounds else "")


# --- Shared PCM helpers --------------------------------------------------------

# A module that slices hands `callback` at most this much audio per call:
# `AudioPlayer.feed` blocks for the whole chunk, and `stop` is polled between
# slices, so a cancel is honored within one slice's playback whatever the
# backend's own chunking.
SLICE_SECONDS = 0.1


def slice_bytes_for(sample_rate: int) -> int:
    """The byte count of ``SLICE_SECONDS`` of signed 16-bit mono PCM at
    ``sample_rate``: the slice size a module feeds in."""
    return 2 * max(1, int(sample_rate * SLICE_SECONDS))


def float_to_int16_pcm(samples: Any) -> bytes:
    """Convert float samples (any array-like, values in about [-1, 1],
    flattened to mono) to signed 16-bit little-endian PCM bytes: clip to ±1.0,
    scale by 32767. The audio-format contract's encoding half, for the
    libraries and APIs that yield float waveforms. The input is not modified.
    """
    arr = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


class PCMFeeder:
    """Feed a backend's PCM chunks to ``callback`` whole-sample aligned and in
    bounded slices, polling ``stop``.

    One per utterance, on the worker thread. ``feed(data)`` prepends what the
    previous call left over (a sample split across two chunks), keeps the
    trailing partial sample of ``sample_bytes`` for the next call, converts the
    whole samples through ``convert`` when given (mistral: float32 bytes to
    int16), and hands the result to ``callback`` in slices of at most
    ``slice_bytes``, checking ``stop`` before each. It returns ``False`` as
    soon as ``stop`` is seen (the rest of the chunk is dropped), ``True``
    otherwise. A partial sample left at the end of the stream is never fed.
    """

    def __init__(
        self,
        callback: Callable[[bytes], None],
        stop: threading.Event,
        *,
        slice_bytes: int,
        sample_bytes: int = 2,
        convert: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self._callback = callback
        self._stop = stop
        self._slice_bytes = slice_bytes
        self._sample_bytes = sample_bytes
        self._convert = convert
        self._carry = b""

    def feed(self, data: bytes) -> bool:
        data = self._carry + data
        whole = len(data) - len(data) % self._sample_bytes
        data, self._carry = data[:whole], data[whole:]
        if not data:
            return True
        if self._convert is not None:
            data = self._convert(data)
        for start in range(0, len(data), self._slice_bytes):
            if self._stop.is_set():
                return False
            self._callback(data[start : start + self._slice_bytes])
        return True
