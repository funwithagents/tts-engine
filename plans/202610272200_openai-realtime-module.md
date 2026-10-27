# OpenAI Realtime module (`openai`)

**Status:** Done

Implements [specs/modules/openai.md](../specs/modules/openai.md), item 5 of `analysis/api-currency-audit.md`: a new `openai` module on OpenAI's Realtime API (`gpt-realtime-2.1-mini`), the successor of the `/v1/audio/speech` models that shut down on 2027-01-06. One socket per utterance, one out-of-band `response.create` per text (a long text sliced, a text stream grouped into sentences), base64 PCM deltas fed through `PCMFeeder`, cancel by `response_id`, every socket closed off the worker. Leaves out the spec's deferrals: a socket held across utterances, overlapped sentence responses, custom voices, `reasoning.effort`, `base_url`. Must land before 2027-01-06.

## Scope

- `src/tts_engine/modules/openai/__init__.py`, `module.py` — `OpenAIModule`: lazy import of `openai` and `websockets.sync.client`, config validation, the session and response payloads, the worker/reader pair over a bounded inbox, `response.done` / `error` handling, cancel and close.
- `src/tts_engine/modules/__init__.py` — `REGISTRY["openai"]`.
- `pyproject.toml` — `openai = ["openai[realtime]>=3.37,<4"]`, added to `all`; `uv.lock` refreshed.
- `tests/modules/test_openai.py` — faked-SDK unit tests (spec, "Testing").
- `tests/modules/test_lazy_imports.py` — the `openai` row in the registry set and the construction loop.
- `tests-e2e/support.py` — the `openai` `MODULES` row; `_REQUIRED_IMPORT` values may name several libraries, `openai` needing both `openai` and `websockets`.
- `examples/config.openai.json` — a complete config.
- `specs/modules/openai.md` — new, `Stable` → `Implemented` with this plan.
- `specs/_index.md`, `specs/project.md`, `specs/testing.md`, `specs/ci.md`, `specs/modules/openai-compatible.md`, `README.md`, `AGENTS.md` — the new module, extra, row and package.
- `analysis/api-currency-audit.md` — item 5 ticked (not committed: `analysis/` stays local).

## Steps

1. Probe the live Realtime socket (scratch scripts, not committed) for what the audit left open: the GA `session.update` shape, event order, handshake and per-response time to first audio, verbatim fidelity on a question, an injection, French, numbers and a long paragraph, `speed`, `reasoning.effort`, cancel with and without `response_id`, and how a bad key, model and voice are reported. Findings that shaped the design: errors arrive as `error` events on an open socket, not as a failed handshake; a bare `response.cancel` does not reach an out-of-band response; the handshake (~1.2 s) dominates a short sentence's latency, hence one socket per utterance.
2. Spec (done before code), then the extra and the lock.
3. Module: validation and payloads in `__init__`; `_synthesize(texts, callback, stop)` opening the socket lazily at the first non-blank text and running one response per text; the reader thread; `synthesize_text` over `slice_text(text, 1500)`, `synthesize_text_stream` over `segment_sentences`.
4. Unit tests with a scripted fake connection; the lazy-import row.
5. Live row, example, docs.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest`; live `-k openai` (the new row and `openai-compatible`) via `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k openai'`; an engine-level run with a multi-sentence text stream and a cancel. Mark this plan `Done` (here and in [_index.md](_index.md)) and the spec `Implemented` only once all pass.

## Result

Unit tier 625 cases (29 new, the new file under 0.5 s), live `-k openai` 10/10 on both OpenAI rows. One finding past the probes: OpenAI's close handshake takes a steady 2.1 s even after a completed response, so every socket is closed off the worker, not only an aborted one. Module level: a 31-character text gave first audio at 1.2 s; a 2040-character text went out as two responses, 92.6 s of audio in 14.3 s; a bad voice failed as `TTSError` naming `invalid_value`. Engine level: a four-sentence `say_stream` gave 10.3 s of audio with first audio at 1.6 s; a cancel surfaced in 0.018 s.
