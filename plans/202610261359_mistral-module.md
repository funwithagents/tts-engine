# Mistral module

**Status:** Done

Implements [specs/modules/mistral.md](../specs/modules/mistral.md): an API-backed `mistral` backend on Mistral's hosted Voxtral TTS through the `mistralai` 3.x SDK, behind a `mistral` extra with a lazy import, streaming `pcm` (float32, 24 kHz) converted to int16 and fed in 100 ms slices, both input shapes (`synthesize_text`, and `synthesize_text_stream` by sentence grouping), faked-SDK unit tests and a live `MODULES` row gated on `MISTRAL_API_KEY`. Deliberately leaves out the spec's deferrals: sentence prefetching, `ref_audio` / `phoneme_hints` / `conversation_history`, and native text input.

## Scope

- `src/tts_engine/modules/mistral/__init__.py` — re-exports `MistralModule`.
- `src/tts_engine/modules/mistral/module.py` — `MistralModule`: key resolution, validation, lazy `from mistralai.client import Mistral`, `_generate`, both synthesis methods.
- `src/tts_engine/modules/__init__.py` — import and `REGISTRY["mistral"]`; docstring lists the package.
- `pyproject.toml` — `mistral = ["mistralai>=3.5,<4"]` extra, added to `all`; `uv.lock` refreshed.
- `tests/modules/test_mistral.py` — unit tests against a faked `mistralai`.
- `tests/modules/test_lazy_imports.py` — poison `mistralai`, expect `mistral` in the registry and its install hint.
- `tests-e2e/support.py` — `_REQUIRED_IMPORT["mistral"] = "mistralai"` and a `MODULES` row.
- `examples/config.mistral.json` — the spec's example config.
- `.github/workflows/ci.yml` — map the optional `MISTRAL_API_KEY` secret in the live step.
- `specs/modules/mistral.md` — module files join its frontmatter (step 8); status to `Implemented`.
- `specs/_index.md` — intro names the Mistral API; a `mistral-module.md` row.
- `specs/tts-module-interface.md` — `mistral/` in the package list, the `REGISTRY` snippet, the API-backed "Module kinds" row; `segment_sentences`' user list.
- `specs/architecture.md` — `mistral/` in the diagram, the providers sentence, a `modules/mistral/module.py` row in "Component responsibilities".
- `specs/project.md` — layout list and extras table (also catching up the missing `phonon` there), `mistralai` in the lazy-import guard's poisoned list, Intel macOS extras.
- `specs/testing.md` — the `mistral` row in the `MODULES` paragraph and the `_REQUIRED_IMPORT` sentence.
- `specs/ci.md` — the `MISTRAL_API_KEY` secret and its expected skips.
- `README.md` — intro, install lines, module table row, a `### mistral` section, examples list, the text-stream sentence, troubleshooting.
- `AGENTS.md` — `modules/mistral/` in the layout row and the `modules/` map row.

## Steps

1. **Packaging.** Add the `mistral` extra after `gradium` and put it in `all`; `uv lock` and `uv sync --dev`. Confirm `uv run python -c "from mistralai.client import Mistral"` imports.

2. **Module** (`modules/mistral/module.py`, `__init__.py`).
   - Constants: `_SAMPLE_RATE = 24000`, `_DEFAULT_MODEL = "voxtral-mini-tts-3-0"`, `_SLICE_SAMPLES = 2400` (100 ms).
   - `__init__`: lazy import first (as ElevenLabs/Gradium do), then `_resolve_api_key` (the ElevenLabs rules, messages naming "Mistral module"), `voice_id`, `model`, `expressiveness` (`isinstance` number, not bool, `math.isfinite`, `>= 1.0`), `server_url`. Build `Mistral(api_key=key, server_url=...)` with `server_url` only when set; keep `self._request_kwargs` = `model`, `voice_id`, `response_format="pcm"`, `stream=True`, `expressiveness` when set.
   - `_generate(text, callback, stop)`: open `self._client.audio.speech.complete(input=text, **kwargs)` inside a `try` → `TTSError("Mistral request failed: …")`; `with events:` loop while not `stop`: `next(events)` and the base64 decode inside the provider `try`; skip non-delta events, `break` on `speech.audio.done`; prepend the carried partial sample, keep `len % 4` bytes for the next delta, convert with `_to_int16_pcm`, then feed `pcm[i:i + 2*_SLICE_SAMPLES]` slices, checking `stop` before each.
   - `synthesize_text`: `run_cancellable_worker` around `_generate`. `synthesize_text_stream`: `run_text_stream_worker` with a worker iterating `segment_sentences(pieces)` → `_generate` per sentence, returning early if `stop` is set.
   - `log = logging.getLogger(__name__)`; no per-chunk logging.

3. **Registry.** Import `MistralModule`, add `"mistral": MistralModule`, list `mistral/` in the docstring.

4. **Unit tests** (`tests/modules/test_mistral.py`). Fake `mistralai` and `mistralai.client` in `sys.modules` (`types.ModuleType`/`SimpleNamespace`): a `Mistral` class recording `__init__` kwargs; `audio.speech.complete(**kw)` recording kwargs and returning a `_FakeEvents` (context manager + iterator over scripted `SimpleNamespace(event=…, data=…)`, `closed` flag, optional raise-on-next, optional blocking mode that signals `reached` and waits). Cover every bullet of the spec's "Testing → Unit" list. Keep to the 5-second budget: no sleeps beyond the 50 ms settle check, no network.

5. **Lazy-import guard.** `"mistralai"` in the poisoned names, `"mistral"` in the expected registry set, `("mistral", {"type": "mistral", "api_key": "k", "voice_id": "v"})` in the construction loop.

6. **Live row.** `_REQUIRED_IMPORT["mistral"] = "mistralai"`, a `_MISTRAL_CONFIG` (`api_key_env: "MISTRAL_API_KEY"`, the James preset UUID) and `pytest.param("mistral", _MISTRAL_CONFIG, id="mistral")`. Add `MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}` to the CI live step.

7. **Example and docs.** `examples/config.mistral.json`; the spec, README, AGENTS.md and cross-spec edits listed in Scope.

8. **Spec frontmatter.** Add `src/tts_engine/modules/mistral/__init__.py`, `src/tts_engine/modules/mistral/module.py` and `tests/modules/test_mistral.py` now that they exist.

9. **Statuses.** Spec to `Implemented` (file and index), this plan to `Done` (here and in [_index.md](_index.md)).

## Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright` — clean.
- `uv run pytest` — new unit tests and the lazy-import guard pass; the tier stays within its budget.
- `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k mistral'` — every conformance scenario passes against the real API.
- Then mark this plan `Done` and the spec `Implemented`, in the files and both indexes.
