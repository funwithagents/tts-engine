# Deepgram and Cartesia: one socket held for the module's lifetime

**Status:** Done

Implements the settled held-socket designs in `specs/modules/deepgram.md` and `specs/modules/cartesia.md`, following the pattern the `openai` module landed in [202610280911_openai-held-socket.md](202610280911_openai-held-socket.md): the socket opens at construction, every utterance runs over it, it is reconnected when dead and replaced before an utterance (never inside one) when it is too idle or too old, events of earlier utterances are filtered out, and a cancel stops the backend's work without closing. Gradium was measured and decided against (last section). It deliberately leaves out reconnecting in the background when a socket drops (the next utterance reconnects instead), application keepalives, and any config knob.

Why: measured 2026-10-28 through each module's own client, the per-utterance handshake is about half of time to first audio or more — Deepgram 285–455 ms of a 636–658 ms first audio (held socket: 186–208 ms), Cartesia 119–160 ms of 207–263 ms (held socket: 100–107 ms).

## Shared across both modules

- **Host sleep.** `time.monotonic()` does not advance while the host sleeps (`mach_absolute_time()` on macOS, `CLOCK_MONOTONIC` on Linux), so an idle check on it alone misses a socket left half-open by a sleep. Both modules stamp a socket's last message on both clocks and replace it before an utterance when the wall clock ran more than `_SLEPT = 20.0` s ahead of the monotonic one since then (the Deepgram rule, step 1 / step 5 below). Cartesia's step 5 gains this check next to its `_IDLE_REPLACE`, and `specs/modules/cartesia.md` says so.
- **Live scenario.** `test_module_say_after_cancel` in `tests-e2e/test_modules.py` (Deepgram step 10) runs for every `MODULES` row, Cartesia's included; it is added once.
- **`specs/tts-module-interface.md`** — editorial, one pass covering both modules: "Lifetime and ownership" lists deepgram and cartesia with openai as modules holding a live socket; "What cancellation guarantees" (milestone 2: deepgram stops backend work with `Clear` / `Interrupt`, cartesia cancels its unacknowledged contexts; milestone 3: neither closes a socket per utterance); "Bounded unread audio" (one segment at the server, the count per utterance). `specs/architecture.md` if its module rows mention per-utterance sockets. No status change for those specs.
- **Indexes.** `specs/_index.md`: both rows → `Implemented` and their summaries refreshed (held socket, cancel without closing, idle/age replacement, Deepgram's flush budget). This plan and its `plans/_index.md` row → `Done`.

## Deepgram: one socket held for the module's lifetime

Implements the settled behavior in `specs/modules/deepgram.md` ("One socket, held for the module's lifetime", "Flush budget (v1)", "Threads: sender, reader, worker", "End of utterance", "Cancellation", "Bounded unread audio", "Validation", "Error handling", "Lifetime"). The module opens its speak socket (v1 or v2, by the model prefix) in `__init__` and keeps it across utterances. An utterance ends on its last finished-segment answer, with no `Close`. A cancel or other abort sends `Clear` (v1) / `Interrupt` (v2) and keeps the socket, and the reader drops everything until the answer. The socket is reconnected when dead, and replaced before an utterance in four cases: at 55 minutes of age (Flux's 1 h cap), after 540 s with no message, sent or received (`_IDLE_REPLACE`, inside the 600 s verified alive on both APIs, in Cartesia's check order: dead → reconnect, then idle too long → replace), when the host slept more than 20 s since the socket's last message (wall clock ahead of monotonic), and, on v1, once 16 flushes fall within 60 s (the server allows 20 per socket per minute). It deliberately leaves out an application keepalive (none exists; `websockets`' default 20 s Ping keeps Flux's 60 s inactivity window open), Flux `playback_offset`, and any config knob.

Why: measured 2026-10-28 (spec, "Validated against", held-socket block), the handshake is 285–450 ms of a fresh utterance's 636 ms (v1) / 658 ms (v2) time to first audio at the SDK level. On a held socket first audio is 186 / 208 ms after the first text, and 165 ms after a cancel against 656–665 ms for close-and-reopen.

### Scope

- `src/tts_engine/modules/deepgram/module.py`:
  - a `_Socket` (SDK manager, entered client, `opened`, `last_message` on both clocks (`monotonic`, `wall`), `dead` event and `error`, `closed` guard, `send_lock`, the attached `_Session` or `None`, a `settling` event, a `deque` of flush send times, an `exhausted` flag) with one reader thread for its life;
  - `_open(stop)`, the setup used by `__init__` and reconnects;
  - `_ready_socket(stop)`, which waits for a settling cancel, reconnects a dead socket and rotates an old or flush-spent one;
  - `_drop`, `close()`;
  - `_Session` per utterance: `close_sent` → `final_sent`, plus `close_sent` kept for the `EXCESSIVE_FLUSH` fallback;
  - the sender sends no `Close`, and sends under the socket's `send_lock`, re-checking `stop` inside it;
  - the worker attaches and detaches the session and, on an abort, sends `Clear` / `Interrupt` instead of closing.
- `tests/modules/test_deepgram.py`: fake extensions and new tests (step 9). Existing tests are updated where they assert `CLOSE` or a closed socket after an utterance.
- `tests-e2e/test_modules.py`: one new generic scenario, `test_module_say_after_cancel` (step 10). It runs for every `MODULES` row, so coordinate with the cartesia/gradium parts if they add the same.
- `specs/tts-module-interface.md`: the deepgram mentions under "Lifetime and ownership" (deepgram joins openai as a module holding a live socket; drop it from "a socket's closing tail … after an abort" and from "ElevenLabs, Gradium and Deepgram clients expose no close" → keep the client remark, add the socket), "What cancellation guarantees" (milestone 2: deepgram stops backend work with `Clear` / `Interrupt`, not by closing; milestone 3: drop deepgram) and "Bounded unread audio" (the "at most two of ≤ 900 characters unanswered" text is stale since the one-segment gate; say "one segment", and that the count is per utterance). `specs/architecture.md` line ~212 (the module row) if it mentions per-utterance sockets. Editorial follow-ups; no status change for those specs.
- `specs/modules/deepgram.md`: status `Updated` → `Implemented` once done, plus the measured figures from step 11 if they differ. `specs/_index.md`: mirror the status and refresh the row's summary ("aborted sockets closed off the worker" → "one socket held for the module's lifetime, `Clear`/`Interrupt` on cancel, flush budget").
- No change to `examples/config.deepgram.json`, `pyproject.toml` or the registry. The config fields and the dependency are unchanged (`websockets>=12` comes with the SDK; the installed 15.0.1 pings by default).

### Steps

1. **`_Socket`.** Fields as in Scope. Add `send(fn, *args)`, which takes `send_lock` and calls `fn`; a failure calls `fail(exc)` (records `error`, sets `dead`) and raises `TTSError("Deepgram request failed: …")`. Add `close_in_background()`, which runs once and closes on a daemon `deepgram-closer` thread through the existing `_close(manager)` (DEBUG log on failure). Add `flushes_in_window(now)`, which prunes the deque to the last 60 s and returns its length. Add `touch()`, which stamps `last_message` with `(time.monotonic(), time.time())`. It is called by `send` and by the reader on every message. Add `idle_for()`, which returns `(mono_idle, wall_idle)`. Module constants next to `_POLL_SECONDS`, each with a one-line comment citing the measured or documented limit:
   - `_ROTATE_AFTER = 55 * 60.0` (Flux `NET-0003`, 1 h);
   - `_IDLE_REPLACE = 9 * 60.0`, commented: inside the 600 s verified alive on both v1 and v2; Deepgram documents no idle limit (Flux's 60 s window is reset by the default pings);
   - `_SLEPT = 20.0` (one `websockets` ping interval; `monotonic` stops while the host sleeps, on macOS `mach_absolute_time()` and on Linux `CLOCK_MONOTONIC`);
   - `_FLUSH_WINDOW = 60.0`, `_FLUSH_BUDGET = 16` (v1: 20 per socket per 60 s).
2. **Reader `_read(socket)`, one per socket.** For each message, under the attached session's condition (or a socket-level lock when none is attached):
   - While `settling` is set: drop `bytes` and the finished-segment type. On the cancel answer, clear `settling` and notify. The answer is `Cleared` on v1; on v2 it is `SpeechInterrupted`, or a `Warning` whose code is in `{"INTERRUPT_IN_PROGRESS", "NO_AUDIO_GENERATED", "INVALID_INTERRUPT_OFFSET"}`.
   - With a session attached: the current `_read` body (`last_message`, `unread`, `flushes_answered`, `inbox.put`).
   - With neither: log at DEBUG (a v2 `Error` before an idle close lands here) and drop.
   - On a `Warning` with code `EXCESSIVE_FLUSH`, set `socket.exhausted` and, if a session is attached, `session.flush_refused = True` (and notify).
   - When the iteration ends or raises: `socket.fail(exc or _ClosedByServer)` first, then put the exception / `_END` on the attached session's inbox, if any.

   Attaching and detaching is done by the worker under the same lock, so a message goes either to the session or nowhere.
3. **Setup `_open(stop) -> _Socket | None`.** `api.connect(**self._connect_kwargs)` → `open_cancellable(manager.__enter__, lambda _s: _close(manager), stop=stop, timeout=self._timeout, what="Deepgram socket")`. `None` when cancelled. Exceptions are re-raised (`__init__` and `_ready_socket` map them). Then build `_Socket`, start the `deepgram-reader` daemon thread, and return it.
4. **Construction connects.** At the end of `__init__`: `self._socket = self._open(threading.Event())`. Map a `websockets.exceptions.InvalidStatus` with `exc.response.status_code in (400, 401, 403)` to `ConfigError(f"{_LABEL}: handshake rejected: HTTP {status}")`, chained. Do the check by duck typing (`getattr(getattr(exc, "response", None), "status_code", None)`), so the module still imports nothing at top and the fake needs no `websockets`. Anything else becomes `TTSError(f"Deepgram request failed: {exc}")`. There is no client close to call on failure (`DeepgramClient` has none).
5. **`_ready_socket(stop)`, run in `_synthesize` before the sender starts.** In order:
   - If the socket is not `None` and is `settling`, wait on it (polling `stop` every `_POLL_SECONDS`, up to `timeout`). On timeout, `_drop` it. If `stop` is set, return `None`.
   - If it is `None`, `dead` or `exhausted`, close the old one in the background and reopen.
   - Replace it (log the reason at DEBUG, close the old one in the background, reopen) if any of these holds:
     - `monotonic() - opened > _ROTATE_AFTER`;
     - `max(mono_idle, wall_idle) > _IDLE_REPLACE`;
     - `wall_idle - mono_idle > _SLEPT` (the host slept);
     - on v1 only, `flushes_in_window(now) >= _FLUSH_BUDGET`.

   A failed reopen raises `TTSError` (an `InvalidStatus` included) and leaves `self._socket = None`. `_open` returning `None` (cancelled during the handshake) ends the utterance with nothing sent. `synthesize_text` with a blank text (`not text.strip()`) returns before `run_cancellable_worker`, touching nothing. `synthesize_text_stream` always runs the checks at worker start.
6. **Utterance over the held socket in `_synthesize`.** Create the `_Session`, attach it under the lock, start the sender, run `_receive`. In `finally`:
   - `completed = session.done()`, then `stop.set()`, then detach the session.
   - If `completed`, or the socket is dead, or it was consumed by the `Close` fallback (`session.close_sent`), stop there.
   - If the failure leaves the socket's state unknown, `_drop`. That is a stall (raise a private `_Stall(TTSError)` subclass from `_check_stall` so `finally` can tell), or a refused mid-utterance flush.
   - Otherwise, set `socket.settling` and send `send_clear()` / `send_interrupt()` through `socket.send`. A failure is logged at DEBUG; the socket is then dead.

   Never `__exit__` the manager here. Remove the old aborted-close path.
7. **Sender `_send`.** Every `send_text` / `send_speak` / `send_flush` goes through `socket.send` with a `stop` check inside the lock. Only the final `Flush` needs the error path: a mid-utterance send error is recorded in `sender_errors` as today. `flush()` appends `monotonic()` to `socket.flush_times`. Remove `send_close` from the normal end: after the final flush, set `session.final_sent = True` under the condition. An empty stream sets `final_sent` with nothing sent. `_Session.done()` becomes `final_sent and flushes_answered >= flushes_sent`.
8. **`EXCESSIVE_FLUSH` fallback in `_receive`.**
   - If `session.flush_refused` is set and `final_sent` is true, send `send_close()` through `socket.send` once and set `session.close_sent`. From then on `_END` (or a connection error) is the normal end, as in the current `close_sent` branch. A `done()` check must not end the utterance before that close, since the last `Flushed` never comes.
   - If `flush_refused` is set with `final_sent` false, raise `TTSError("Deepgram request failed: flush rate limit exceeded")`; step 6 drops the socket.
   - `_END` with neither `close_sent` nor `done()` stays `TTSError("… the server closed the connection")`.
9. **Tests (`tests/modules/test_deepgram.py`).**
   - **Fake.** `_FakeBackend.connects` counts connects. The socket implements `send_clear` / `send_interrupt`: it enqueues `stale` audio (configurable) and a held-back finished-segment answer, if any, then `Cleared` (v1) / `SpeechInterrupted` (v2), unless `hold_cancel_answer` is set. Each `Flush` beyond `flush_limit` is answered `Warning EXCESSIVE_FLUSH` instead of the finished-segment answer. `server_close()` ends the iterator (idle death). `send_close` keeps today's behaviour. `__exit__` closes the socket it entered, not `sockets[-1]`. A helper `_wait_closed(socket)` polls with a 1 s cap, since closing is off-thread.
   - **Update existing tests.**
     - `test_text_then_flush_then_close_and_audio_unchanged` → `sent == ["Hello ", "world.", "FLUSH"]`, socket not closed.
     - `test_long_text_is_cut_into_flushed_segments_at_sentence_ends` → `sent[-1] == "FLUSH"`.
     - `test_sender_waits_for_the_segment_before_the_next_one` → ends on `FLUSH`.
     - `test_text_stream_sends_word_groups_then_flush_and_close` → no `CLOSE`.
     - `test_empty_text_stream_closes_without_text_or_flush` → `sent == []`.
     - `test_flux_model_runs_on_speak_v2` → `sent[-1] == "FLUSH"`.
     - `test_flux_utterance_ends_on_speech_metadata_not_flushed` → wait on `FLUSH`, not `CLOSE`.
     - `test_callback_error_propagates_unchanged_and_closes` → `…_and_clears`: `CLEAR` sent, socket open.
     - `test_cancel_mid_audio_stops_callbacks_before_raising` → `CLEAR` (parametrize `_BOTH`: `INTERRUPT` on v2), socket open.
     - `test_text_stream_source_error_propagates_unchanged` and `test_text_stream_cancel_while_the_server_is_silent_stops_promptly` → cancel message sent, no `CLOSE`, socket open.
     - `test_server_closing_before_close_is_tts_error` → a server close mid-utterance is `TTSError` and the next `_say` reconnects (`connects == 2`).
     - `test_connect_error_is_tts_error` and `test_handshake_slower_than_timeout_is_tts_error` → raise at `DeepgramModule(...)`.
     - `test_cancel_during_the_handshake_surfaces_promptly_and_closes_late` → exercised on a reconnect: construct, `server_close()`, gate the next connect, cancel.
     - `test_defaults_reach_the_socket` and `test_custom_values_reach_the_socket` → kwargs recorded at construction.
   - **New tests, one behaviour each.**
     - Construction connects once, before any `say`.
     - `InvalidStatus`-like exceptions with status 400 / 401 / 403 → `ConfigError` naming the status (parametrized); 429 → `TTSError`.
     - Two `_say`s → `connects == 1`, each ending on its own answer.
     - A blank text sends nothing.
     - `server_close()` while idle → the next `_say` reconnects and speaks; a reconnect failure → `TTSError`, and the call after that tries again (`connects == 3`).
     - Rotation under a patched `deepgram_module.time.monotonic`, with `opened` 56 minutes back → reconnect before the first text.
     - Idle replacement under a patched `deepgram_module.time.monotonic` (and `time.time` moved in step): `last_message` 541 s back → closed off the worker and reconnected before anything is sent (`connects == 2`, the old socket closed, nothing sent on it); 539 s → same socket.
     - The slept-host rule, with the wall clock 21 s ahead of the monotonic one since `last_message` → reconnect; 19 s → none.
     - `last_message` refreshed by both a send and a received message.
     - The v1 flush budget: 16 recorded flush times inside 60 s → reconnect; 15 → none; on v2, 16 → none.
     - Cancel mid-audio with `stale` audio and a held finished-segment answer queued before the cancel answer → none of it reaches the next `_say`'s callback, and the next utterance's `unread` starts at 0.
     - `hold_cancel_answer` → the next `_say` waits, then proceeds once released, on the same socket.
     - A cancel answer that never comes with `timeout: 0.05` → the next `_say` reconnects.
     - The sender racing a cancel: a `pieces` source that yields again right after `stop` → nothing after `CLEAR` in `sent`.
     - `EXCESSIVE_FLUSH` on the final flush (`flush_limit=0`, one-segment text) → `CLOSE` sent, the audio complete, the call returns normally, the next `_say` reconnects.
     - On a mid-utterance flush (a 2000-character text, `flush_limit=1`) → `TTSError` "flush rate limit", socket dropped.
     - A stall drops the socket (the next `_say` reconnects).
     - A v2 `Error` while idle → logged at DEBUG, the next `_say` reconnects.
     - `close()` closes the held socket off the calling thread (returns before a slow `__exit__` gated in the fake finishes); after `server_close()` it closes nothing.

     Keep every wait event-driven with the 5 ms patched poll so the file stays well inside the unit tier's 5 s budget.
10. **Live scenario.** In `tests-e2e/test_modules.py`, add `test_module_say_after_cancel`, parametrized over `MODULES`: on one engine with `_PacedCaptureSink`, cancel a `say(_LONG_TEXT)` with `_cancel_after_first_chunk`, then `await engine.say("Hello again.")` and assert new audio was fed (and `drains == 2`). It costs one short utterance per row. Run `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k deepgram'`: both rows green.
11. **Benchmark.** Rerun the deepgram entries of the TTFA benchmark (`uv run python analysis/ttfa-benchmark/bench_ttfa.py --runs 20 --only "deepgram/*"`, keys sourced as above). Expected:
    - mean TTFA about 0.2–0.35 s in every cell, against 0.49–0.64 s before;
    - `load s` now includes the handshake (about 0.3–0.5 s);
    - `first call` no longer above the measured runs.

    Record the numbers in `analysis/ttfa-benchmark/STATUS.md` and, if they differ from the spec's figures, in the spec's held-socket block.
12. **Statuses.** `specs/modules/deepgram.md` and its `specs/_index.md` row → `Implemented` (the plan itself is closed in the shared section).

### Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`, `uv run pytest` all clean; `tests/modules/test_deepgram.py` covers every behaviour in step 9, and the unit tier stays under 5 s.
- Live: `tests-e2e -k deepgram` green on both rows (`deepgram`, `deepgram-flux`) with `DEEPGRAM_API_KEY` set, the new cancel-then-speak scenario included.
- Benchmark: the deepgram entries' mean TTFA at or under 0.35 s in all cells.

## Cartesia: one socket held for the module's lifetime

Implements the settled behavior in `specs/modules/cartesia.md` ("One socket, held for the module's lifetime", "Routing by context", "Threads: sender, reader, worker", "End of utterance", "Cancellation", "Validation", "Error handling", "Lifetime"). The `cartesia` module opens its TTS socket in `__init__` and keeps it across utterances — reconnecting when it dies, replacing it once idle 240 s (the server closes at 300 s, pings do not count), routing events to the current utterance by `context_id`, cancelling only unacknowledged contexts and never closing on the way out — so no `say()` pays the ~120 ms handshake. It leaves out reconnecting inside an utterance (spec open question 1), a keepalive and any config knob.

Why: measured 2026-10-28, first audio on a fresh socket is 207–263 ms (handshake 119–160 ms + 88–102 ms), on a held socket 100–107 ms.

### Scope

- `src/tts_engine/modules/cartesia/module.py` — a new `_Socket` (SDK manager, connection, `dead` event + `error`, `last_message`, the attached `session`, a lock, a once-only `close_in_background`); `_open(stop) -> _Socket | None` (handshake through `open_cancellable`, reader started) used by `__init__` and by the worker's reconnect; `_ready_socket(stop)` (dead → reconnect, idle past `_IDLE_REPLACE` → replace); `_drop(socket)`; the reader becomes per-socket and routes by `context_id`; `_synthesize` takes the socket, attaches/detaches its `_Session`, and on the way out cancels only unacknowledged contexts and closes nothing (except `_drop` after a stall or transport failure); `close()` closes the socket off the calling thread, then the client.
- `tests/modules/test_cartesia.py` — fake extended (per-manager socket close, connect count, server-side close, `done` after `cancel`, foreign events, module teardown); new tests for the held socket; existing socket-close assertions updated.
- `specs/modules/cartesia.md` — status `Updated` → `Implemented` once done; no text change expected. `specs/_index.md` — mirror the status.
- `specs/tts-module-interface.md` — editorial: "Lifetime and ownership" lists cartesia with openai as a module holding a live socket (not "an utterance owns its transport", not "cartesia closes off the worker after an abort"); "What cancellation guarantees" milestone 2 says cartesia cancels its contexts (the socket is kept) and milestone 3 drops cartesia from the per-utterance closers. Coordinate with the deepgram/gradium sections, which touch the same paragraphs.
- No change to `examples/config.cartesia.json`, `tests-e2e/`, `pyproject.toml` or the registry. The live row exercises the held socket (the cancel scenario speaks again on the same module).

### Steps

1. **`_Socket`.** Fields: `manager`, `ws`, `dead: threading.Event`, `error: Exception | None`, `last_message: float` (monotonic, set at open, on every send by the sender/worker and every message by the reader), `session: _Session | None`, `lock: threading.Lock`, `closed: threading.Event`. Methods: `fail(exc)` (first error wins, sets `dead`), `attach(session)` / `detach()` under `lock`, `close_in_background()` (once, on a `cartesia-closer` daemon thread calling `manager.__exit__`, failures at DEBUG). Constants next to the existing ones: `_IDLE_REPLACE = 240.0` with a comment on the 300 s server idle close and pings not counting.
2. **Reader per socket `_read(socket)`.** Loop `ws.parse_event(ws.recv_bytes(timeout=_POLL_SECONDS))`; on `TimeoutError` continue; on any other exception `socket.fail(exc)`, put `exc` into the attached session's inbox if any, return. For each event: `socket.last_message = now`; take `session = socket.session` (under `socket.lock`); if `session` is not `None` and `event.context_id in session.opened` (a new `set[str]` filled by `_Session.open`), do today's bookkeeping under `session.changed` (`last_message`, `last_activity`, `unread`) and `session.inbox.put`; else `log.debug("Dropping a %s of foreign context %s", ...)`. An event with no `context_id` goes to the attached session (an `error` then fails it), else DEBUG. Stops once `socket.closed` is set.
3. **`_open(stop)`.** `manager = self._client.tts.websocket_connect(websocket_connection_options={"open_timeout": timeout})`; `ws = open_cancellable(manager.__enter__, lambda _ws: _close(manager), stop=stop, timeout=self._timeout, what="Cartesia socket")`; `None` → return `None`; build `_Socket`, start the `cartesia-reader` daemon, return it. Exceptions propagate to the caller (mapped there).
4. **Construction connects.** At the end of `__init__`: `self._socket = self._open(threading.Event())`. Map `websockets.exceptions.InvalidStatus` with `response.status_code in (401, 403)` to `ConfigError(f"{_LABEL}: the server rejected the key (HTTP {code})")` (chained; import `InvalidStatus` lazily inside `__init__` next to `websockets.sync.client`), anything else to `TTSError(f"Cartesia request failed: {exc}")`. On any failure close the client before raising.
5. **`_ready_socket(stop)` and `_drop(socket)`.** As in the openai module: missing or `dead` → log, `close_in_background` the old one, `self._socket = self._open(stop)` (failure → `TTSError`, `self._socket` stays `None`); else idle past `_IDLE_REPLACE` on either clock, or the host slept (wall clock more than `_SLEPT` ahead of the monotonic one since `last_message`; see "Shared across both modules") → same replacement; else return it. `last_message` is stamped on both clocks, as in Deepgram's `_Socket.touch()`. Tests: the slept-host rule at 21 s → replaced, 19 s → not. `_drop` clears `self._socket` if it is that socket and closes it off-thread.
6. **`_synthesize(pieces, callback, stop)`.** `synthesize_text` returns before the worker for a blank text (`not text.strip()`). The worker: `socket = self._ready_socket(stop)`, `None` → return; `session = _Session()`; `socket.attach(session)`; start the per-utterance sender with `socket.ws`; `_receive(...)` as today. `finally`: `stop.set()`; under `session.changed` collect `[ctx for id, ctx in session.contexts if id not in session.done]`; cancel each (best effort, DEBUG; a send failure `socket.fail`s it); `socket.detach()`. The paths that `_drop` the socket: a stall (`_check_stall` raising) and a transport failure (the inbox exception, a sender/idle-check send failure → `socket.fail` then `_drop`). Remove the closer thread and `_close(connection)` from the per-utterance path. `_Session.contexts` becomes a `dict[str, ctx]` (or keep the list plus `opened`).
7. **Sender.** Unchanged logic, but every send failure calls `socket.fail(exc)` and every push / `no_more_inputs` / context open updates `socket.last_message`. Keep the push-under-lock-after-`stop`-check invariant (it is what makes a late sender harmless on a shared socket); add a comment saying so.
8. **`close()`.** `socket, self._socket = self._socket, None`; `socket.close_in_background()` if any; `self._client.close()`.
9. **Tests — fake.** `_FakeManager.__exit__` closes *its own* socket (not `sockets[-1]`); `_FakeBackend.connects` = `len(connect_kwargs)`; `_FakeWS.server_close(exc=ConnectionError("1000 connection idle timeout"))` enqueues the exception and marks the socket closed; `_FakeContext.cancel` enqueues `done` for a context with no `done` yet (configurable off); a helper to send foreign events (`_event("chunk", "foreign-id", audio=...)`, `done`, `error`). `fake.raise_on_connect` now fails construction; add `fake.fail_next_connect` for reconnect failures. An autouse fixture (or the `fake` fixture's teardown) closes every module the test built (track instances via a `monkeypatch` wrapper on `CartesiaModule.__init__`, or `_FakeBackend.sockets` all closed) so no reader thread polls past its test; keep `_POLL_SECONDS` patched to 0.005.
10. **Tests — new, one behaviour each.** `test_construction_connects_once_with_open_timeout`; `test_rejected_key_at_construction_is_config_error` (fake raises an `InvalidStatus`-like exception with `response.status_code = 401`; the fake `websockets` module, or the real one, provides the class) and `test_other_handshake_failure_at_construction_is_tts_error`, both with `client_closed`; `test_two_utterances_share_one_socket` (one connect, two distinct context ids, socket open); `test_server_close_between_utterances_reconnects` (`server_close` after the first `say`, second `say` succeeds, `connects == 2`); `test_idle_socket_replaced_before_the_first_context` (patch `cartesia_module.time.monotonic` so `last_message` is 241 s old; assert the second socket got the context and the first is closed); `test_reconnect_failure_fails_that_utterance_only`; `test_blank_say_touches_nothing`; `test_foreign_events_are_dropped` (foreign chunk/`done`/`error` interleaved with the utterance's own: callback gets only own audio, no `TTSError`, `unread` back to 0); `test_cancel_keeps_the_socket_and_cancels_only_unacknowledged_contexts`; `test_cancelled_tail_does_not_reach_the_next_utterance` (the cancelled context's late chunk + `done` arriving after the next utterance's first push); `test_late_sender_never_pushes_into_the_next_utterance`; `test_stall_drops_the_socket` (next `say` reconnects); `test_close_closes_socket_and_client` and `test_close_with_a_dead_socket_closes_only_the_client`.
11. **Tests — existing, updated.** `test_defaults_reach_the_context` (connect kwargs recorded at construction); `test_text_is_pushed_then_ended_and_audio_reaches_the_callback` (no `cancel` after `done`, socket **not** closed); `test_connect_error_is_tts_error` → the reconnect path (kill the socket, make the next connect fail); `test_error_event_is_tts_error` and `test_callback_error_propagates_unchanged_and_closes` (rename `…_keeps_the_socket`; socket open, next `say` reuses it); `test_connection_closing_before_done_is_tts_error` (socket dead, next `say` reconnects); `test_cancel_mid_audio_stops_callbacks_before_raising` (socket open; the endless-audio fake must stop for a cancelled context); `test_text_stream_source_error_propagates_unchanged` and `test_text_stream_cancel_while_the_server_is_silent_stops_promptly` (socket open instead of closed); `test_empty_text_stream_sends_nothing` (no context, socket open, no reconnect); `test_cancel_during_the_handshake_surfaces_promptly_and_closes_late` and `test_handshake_slower_than_timeout_is_tts_error` → on a reconnect (server-close the held socket first, then gate the next connect), plus a construction variant of the timeout one. Unit tier stays under its 5 s budget.
12. **Live check.** `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k cartesia'` passes. Then a scratch check (not committed) through `TTSEngine`: three `say`s in a row on one engine → one handshake, first audio of the 2nd/3rd ≈ 0.10–0.15 s against 0.20–0.28 s before; a `say` after a 305 s idle reconnects and speaks; cancel then `say` at once on the same socket. If the TTFA benchmark is rerun (`analysis/ttfa-benchmark/bench_ttfa.py --only "cartesia/*"`), record it in `analysis/ttfa-benchmark/STATUS.md`.
13. **Statuses.** Flip `specs/modules/cartesia.md` and its `specs/_index.md` row to `Implemented` (the plan itself is closed in the shared section).

### Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`, `uv run pytest` clean; `tests/modules/test_cartesia.py` covers every behaviour in steps 10–11 and the unit tier stays under 5 s; no `cartesia-reader` thread alive after the test module (assert in a module-level teardown or check `threading.enumerate()` in one test).
- Live: `tests-e2e -k cartesia` green with `CARTESIA_API_KEY` set.
- Measured: second-utterance first audio on a held socket ≤ 0.15 s through the engine.

## Gradium — decided against holding the connection

We decided not to build this, so this section has no steps. Gradium keeps one WebSocket per utterance. Live measurements on 2026-10-28 (recorded in `specs/modules/gradium.md`, open question 1) show that a held connection still needs its own `setup`, `ready` and a unique `client_req_id` for every utterance. Only the 35–50 ms socket open is saved. Time to first audio fell from 161 ms mean (fresh connection, n=20) to 124 ms mean (held connection, n=20), about 37 ms, or 25–30 ms on the median. That is under the 50 ms bar. Getting even that much would need several new parts: a module-owned long-lived asyncio loop thread (the SDK and its aiohttp session are async and bound to their loop), a reader that never stops receiving (an unread socket is dropped within 90 s because its 20 s pings go unanswered), the deepgram/cartesia unread-audio admission gate (the always-reading reader loses today's TCP backpressure), and direct aiohttp use in place of the SDK's `Tts`. Pre-arming the next request would halve time to first audio (80 ms), but a set-up request with no output is ended after 120 s and holds one of the account's 5 concurrent sessions, so it is ruled out. No code, test, example or other spec changes. `specs/modules/gradium.md` stays `Implemented`, and only open question 1 was edited.

## Deviations found while building

- **Deepgram character budget.** The first benchmark run lost 4 of 48 `aura-asteria-en` syntheses to `1008 DATA-0001` ("too many characters submitted within a recent time window"): about 2400 characters per socket per ~60 s, never reached with a socket per utterance nor at real-time playback (~1000 characters a minute), but reached across utterances by a faster-than-real-time sink on a fast model. The module logs the characters sent on each socket and replaces one holding `_CHAR_BUDGET = 1200` or more within `_CHAR_WINDOW = 60` s before the next utterance, on both APIs; the rerun had none in 96 syntheses. Spec: "Flush and character budgets".
- **Clock tests** (both modules) move the socket's stamps back instead of patching `time.monotonic`, which is the global `time` module that asyncio's loop clock reads too.
- **Results.** Deepgram first audio is 180–233 ms mean in every benchmark cell (n=20; before 494–644 ms), Cartesia 87–166 ms (before 203–274 ms). The new `test_module_say_after_cancel` passed on all 17 live rows. 2 of 192 asteria syntheses failed with a sporadic `1011 NET-0000` server error, not reproduced in an 80-utterance probe; the next utterance reconnects.

## Verification (whole plan)

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`, `uv run pytest` clean; the unit tier stays under its 5 s budget with both modules' new tests.
- Live: `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e'` green, the new cancel-then-speak scenario on every row.
- Each module's own Verification above.

Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
