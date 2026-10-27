# Bounded text bridge and windowed sentence segmenter

**Status:** Done

Implements the settled behaviour in [tts-module-interface.md](../specs/tts-module-interface.md) ("Text-stream helpers": the bounded `run_text_stream_worker` bridge and the `segment_sentences` length bound). Closes findings F1 and F8 of the 2026-10-26 module code review: the bridge pumped the text source into an unbounded queue with no loop turn (an immediately yielding source was consumed whole before the worker ran, and a cancel could not get in), and the segmenter's "length cap" cut at the last boundary anywhere in the buffer, so a 10 000-character piece became a 10 000-character segment. Leaves out: bounding the audio a provider has sent and the worker has not fed yet (spec open question 2), and a configurable `max_chars` (open question 1).

## Scope

- `src/tts_engine/modules/base.py` — `run_text_stream_worker` gains a character bound (`MAX_PENDING_CHARS`, a keyword-only `max_pending_chars` for tests) and the pump awaits room on the loop; `segment_sentences` / `_forced_cut` search the cut inside the first `max_chars` characters, with the single-long-word exception.
- `tests/modules/test_tts_module_interface.py` — the bridge: how far an immediate source runs ahead, one oversized piece still passing, a cancel with an endless immediate source and with an endless empty-only source; the segmenter: one big piece and the same text by character give the same bounded segments, a sentence end past the window loses to a cut inside it, only a long word exceeds the bound (the earlier whitespace test's expectation updated to the bounded result).
- `specs/tts-module-interface.md`, `specs/architecture.md` — the clauses above and the narrowed "never blocked" claim.

## Steps

1. **Bound the bridge** (`base.py`). Keep the loop-thread counter of queued characters; the worker-side `pieces()` reports each piece it takes through `loop.call_soon_threadsafe`, which decrements the counter and sets an `asyncio.Event`; the pump clears and awaits that event while the next piece would push the counter past the bound (and the queue is not empty). Yield the loop (`sleep(0)`) on every empty piece.
2. **Window the segmenter** (`base.py`). A sentence end counts only if its match ends within `max_chars + 1` (the stripped segment is one shorter). Past `max_chars` with no such end, `_forced_cut(buffer, max_chars)` takes the last clause end in `buffer[:max_chars + 1]`, else the last whitespace there, else the first whitespace after it (the long word), else waits for more input.
3. **Tests**, as in Scope; the bound is set low (500 characters, 10 for the oversized piece) so the behaviour shows without a large source.
4. **Specs.** The bridge bullet, the "Length bound" bullet (with the old behaviour noted), the audio-buffering deferral; the architecture threading bullet.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest` — green (721 tests, under 5.5 s locally); the live tier for the providers that group sentences (openai, mistral, gemini) and the fixtures passed with the windowed segmenter.
