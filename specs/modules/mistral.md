---
code:
  - src/tts_engine/modules/mistral/__init__.py
  - src/tts_engine/modules/mistral/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_mistral.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Mistral Module

**Status:** Implemented

## Overview

`mistral` implements `TTSModule` using Mistral's hosted **Voxtral TTS** models (`voxtral-mini-tts-3-0` by default) through the official `mistralai` Python SDK. It asks for raw `pcm` and streams it over server-sent events, so there is no codec to decode; the module's one conversion is float32 → int16. It is one API-backed provider among others — not the default and not the base install — and ships behind the `mistral` packaging extra (see "Dependencies").

Its shape is the ElevenLabs one, without the decode: a synchronous SDK iterator driven from the cancellable worker. For a text stream it **groups the pieces into sentences** (one request per sentence), like the local models: the speech endpoint takes one complete `input` per request and the SDK offers no incremental text input (see "Text stream").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "mistral"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Mistral API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Mistral's own convention is `MISTRAL_API_KEY`). Lets a config file be committed with no secret. |
| `voice_id` | non-empty string | yes | — | The voice: a preset voice's id (a UUID such as `01a14675-dc30-778a-8d01-b5dead5a522a`, "James") or slug (`voice_3150`), or a custom voice's id. The API has no default voice. |
| `model` | non-empty string | no | `"voxtral-mini-tts-3-0"` | Mistral TTS model id. |
| `expressiveness` | number | no | — (API default) | Speaker classifier-free guidance scale (the SDK's wording: "speaker flow-matching CFG scale"); finite and `>= 1.0`. |
| `server_url` | non-empty string | no | — (SDK default `https://api.mistral.ai`) | API base URL, for a regional or self-hosted endpoint. Passed to `Mistral(server_url=...)` only when set. |

### API key resolution

Identical to ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `Mistral(api_key=...)`; the SDK's own environment fallback is never relied on.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `voice_id`, `model` — non-empty strings (`model` defaults when absent).
- `expressiveness` — if present, a finite number (not bool) `>= 1.0`.
- `server_url` — if present, a non-empty string.

`expressiveness` is **omitted from the request when unset**, so the API's own default applies and the module never hardcodes a copy of it. Whether a `voice_id` exists, or a `model` id is valid, is only known to the API: a wrong one fails at the first synthesis as `TTSError` (the API answers 404 `invalid_voice` / 422 `invalid_model`).

### Text markup

Text is sent verbatim. Voxtral TTS 3 reads inline emotion and pace tags in the text itself; the module neither interprets nor strips them.

## Implementation notes

### SDK entry point

```python
from mistralai.client import Mistral  # the 3.x import path; `mistralai.Mistral` is gone

client = Mistral(api_key=key)  # + server_url=... when set
events = client.audio.speech.complete(
    input=text,
    model=self._model,
    voice_id=self._voice_id,
    response_format="pcm",
    stream=True,
    # expressiveness=... only when set
)
with events:  # EventStream: __exit__ closes the HTTP response
    for event in events:
        if event.event == "speech.audio.delta":
            pcm_f32 = base64.b64decode(event.data.audio_data)
            ...
        elif event.event == "speech.audio.done":
            break
```

`complete(..., stream=True)` returns the SDK's synchronous `EventStream`: an iterator of `SpeechStreamEvents` (`event` — the SSE event name — and `data`). `speech.audio.delta` carries base64 `audio_data`; `speech.audio.done` carries the `usage` and ends the utterance. Any other event name is skipped (forward compatibility). One client is built in `__init__` and reused across utterances; the engine's `say` lock means at most one request runs at a time.

### Output format and sample rate

`response_format="pcm"` is headerless **float32 little-endian, mono, 24000 Hz** — confirmed live against `voxtral-mini-tts-3-0` (the `wav` format of the same model reports 24000 Hz mono in its header; the `pcm` bytes read as float32 give a smooth waveform in `[-1, 1]`, read as int16 they are noise). The API has no rate parameter, so `sample_rate` is the constant `24000`.

The module converts to the contract's signed 16-bit ([tts-module-interface.md](../tts-module-interface.md), "Audio format contract"): clip to ±1.0, scale by 32767, `astype("<i2")` — the pocket/phonon conversion. A delta whose byte length is not a multiple of 4 (a sample split across two events — not observed, the API sends whole samples) carries the partial sample over to the next delta; an incomplete sample left at the end of the stream is dropped.

### Feeding in slices

The API runs well ahead of real time and sends large deltas (observed: 0.4 s of audio each for the first five, then 2 s each). `AudioPlayer.feed` blocks until the whole chunk is written, so one 2 s delta handed to `callback` in one call would hold off a cancel for up to 2 s. The module therefore feeds each converted delta in **slices of at most 100 ms (2400 samples)** and polls `stop` between slices, so a cancel is honored within one slice's playback, the same order as Gradium's 80 ms frames.

### Threading and cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"), exactly the ElevenLabs shape: the worker opens the event stream, polls `stop` between events and between slices, and leaves the `with events:` block on every path — normal end, `stop`, error — which closes the HTTP response so the server drops the request. `synthesize_text` re-raises `CancelledError` only once the thread has exited.

### Text stream (grouped)

`synthesize_text_stream` runs one worker through `run_text_stream_worker`, groups the pieces with `segment_sentences`, and makes one `complete(..., stream=True)` request per sentence inside that worker, feeding each sentence's audio before requesting the next — the pocket/phonon structure ([tts-module-interface.md](../tts-module-interface.md), "Local-model modules", last bullet). Audio for sentence one plays while the stream is still delivering sentence two.

Why not native: the speech endpoint takes one `input` per request. The `voxtral-mini-tts-realtime-3-0` model alias is reachable only through Mistral's voice-agents stack (`client.audio.agents` / `client.audio.sessions`), a conversational pipeline rather than verbatim TTS, and the SDK exposes no text-append call for speech. Grouping costs one request's time-to-first-audio between sentences (about 0.4 s measured); see "Open questions".

### Dependencies and lazy import

Requires the `mistral` extra: `pip install tts-engine[mistral]` / `uv sync --extra mistral`, declared in `[project.optional-dependencies]` as `mistral = ["mistralai>=3.5,<4"]` and added to the `all` aggregate. `<4`: the SDK is Speakeasy-generated and its 2.x → 3.x major moved the client to `mistralai.client`; the module relies on that path. The SDK is pure Python (`httpx`, `pydantic`, OpenTelemetry API), no codec: a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `mistral/module.py` must **not** import `mistralai` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    from mistralai.client import Mistral
except ImportError as exc:
    raise ConfigError(
        "The 'mistral' module requires the mistral extra: pip install tts-engine[mistral]"
    ) from exc
```

`tests/modules/test_lazy_imports.py` adds `mistralai` to the poisoned modules and the `mistral` row to its construction loop.

### Error handling

- Any exception raised by the SDK or the transport — a `mistralai.client.errors.MistralError` (`SDKError`, `HTTPValidationError`: bad key, unknown voice, invalid model), an `httpx` connection or read failure mid-stream, an undecodable `audio_data` — is caught and re-raised as `TTSError(f"Mistral request failed: {exc}")`, chained via `raise ... from exc`.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as Mistral request failures: the event iterator is advanced inside the provider `try`, `callback` is invoked outside it.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

## Module ID

Registered in `REGISTRY` as `"mistral"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_mistral.py`)** — `mistralai` is **faked** in `sys.modules` (a stub `Mistral` whose `audio.speech.complete` records its kwargs and returns a scripted event stream with a `closed` flag), so no request is ever made and the fast tier runs without the extra. Covers: `ConfigError` on a missing extra, on a missing/empty key and on each invalid field (parametrized); key resolution (`api_key_env`, literal precedence, unset variable); `server_url` reaching the client only when set; the request kwargs (`response_format="pcm"`, `stream=True`, `model` default, `voice_id`, `expressiveness` only when set); float32 deltas reaching the callback as clipped int16, other events skipped, `speech.audio.done` ending the read; a delta split mid-sample carried over; a 2 s delta fed in 100 ms slices; SDK errors at request time and mid-stream wrapped in `TTSError` with the stream closed, callback errors propagating unchanged; cancellation closing the stream before `CancelledError`; and for `synthesize_text_stream`: one request per sentence, the first sentence's audio reaching the callback before the source has yielded its last piece, and cancellation with the source still open.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`mistral`, `api_key_env: "MISTRAL_API_KEY"`, the "James" preset voice) plus `_REQUIRED_IMPORT["mistral"] = "mistralai"` in `support.py`, so every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is absent ([testing.md](../testing.md), "Live tier"). CI maps an optional `MISTRAL_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `mistralai==3.5.0`

Confirmed from the package and live calls on 2026-10-26:

- `from mistralai.client import Mistral`; `Mistral(api_key=..., server_url=..., ...)`. `from mistralai import Mistral` fails in 3.x.
- `client.audio.speech.complete(*, input, model, voice_id, ref_audio, expressiveness, phoneme_hints, conversation_history, prompt_cache_key, metadata, response_format, stream, ...)`; `response_format` ∈ `pcm`, `wav`, `mp3`, `flac`, `opus`. With `stream=True` it returns `EventStream[SpeechStreamEvents]` (context manager; `close()` closes the `httpx` response).
- Events: `speech.audio.delta` (`data.audio_data`: base64) and `speech.audio.done` (`data.usage`).
- `pcm` = float32 LE mono 24 kHz; deltas of 38400 bytes (0.4 s) then 153600 bytes (2 s); time to first audio ~0.4–0.5 s; closing the stream mid-utterance returns at once.
- No `model` → 422 `invalid_model`; no voice → 400; unknown voice → 404 `invalid_voice`; `expressiveness < 1` → 422. `voxtral-mini-tts-latest` with a `voice_id` → 400 ("voice is not supported for speech v1"), so the default names the dated model, not the alias.

## Open questions

Deferrals only (design is settled):

1. **Prefetching the next sentence.** In the grouped text stream, sentence N+1's request starts only once sentence N is fed, which adds one time-to-first-audio (~0.4 s) of silence between sentences. A worker could request N+1 while N plays, since the API runs ahead of real time; that is a second thread and a bounded queue. Measure on the live tier first. Pocket and phonon have the same gap and the same deferral.
2. **`ref_audio` voice cloning, `phoneme_hints`, `conversation_history`.** The API supports cloning from a reference clip per request, IPA pronunciation hints and conversational conditioning. Each is a config field away (`ref_audio` as a file resolved with `resolve_path` and base64-encoded); none is needed to speak.
3. **Native text input** if Mistral adds a text-append speech socket to the SDK: the module would move to the Gradium/ElevenLabs sender/receiver shape.
