---
code:
  - src/tts_engine/modules/openai/__init__.py
  - src/tts_engine/modules/openai/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_openai.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# OpenAI Module

**Status:** Implemented

## Overview

`openai` implements `TTSModule` with OpenAI's speech endpoint (`POST /v1/audio/speech`; models `gpt-4o-mini-tts` by default, `tts-1`, `tts-1-hd`) through the official `openai` Python SDK. It asks for raw `pcm` and reads the chunked HTTP body as it arrives, so there is no codec to decode and no conversion: the bytes are already the contract's signed 16-bit little-endian mono. It is one API-backed provider among others — not the default and not the base install — and ships behind the `openai` packaging extra (see "Dependencies").

With `base_url` (and `sample_rate`) it also reaches any **OpenAI-compatible** `/v1/audio/speech` server: vLLM-Omni, SGLang Omni, `mlx_audio.server`, Kokoro-FastAPI and the like. (`pocket-tts serve` is not one: it serves its own `/tts` route only.) Such a server must honor `response_format="pcm"`; one that answers with a WAV file is rejected with a clear `TTSError` (see "OpenAI-compatible servers").

Its shape is the Mistral one: a synchronous SDK iterator driven from the cancellable worker. For a text stream it **groups the pieces into sentences** (one request per sentence): the endpoint takes one complete `input` per request (see "Text stream").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "openai"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | API key, given literally. A keyless compatible server still needs one (the SDK refuses to build a client without), so give it any placeholder such as `"unused"`. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (OpenAI's own convention is `OPENAI_API_KEY`). Lets a config file be committed with no secret. |
| `voice` | non-empty string | yes | — | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin`, `cedar` (OpenAI recommends `marin` or `cedar` with `gpt-4o-mini-tts`), or whatever names a compatible server uses. The API has no default voice. |
| `model` | non-empty string | no | `"gpt-4o-mini-tts"` | Speech model id: `gpt-4o-mini-tts`, a dated snapshot such as `gpt-4o-mini-tts-2025-12-15`, `tts-1`, `tts-1-hd`, or a compatible server's model. |
| `instructions` | non-empty string | no | — | Free-text voice direction (tone, pace, accent, emotion). Honored by `gpt-4o-mini-tts`; `tts-1`/`tts-1-hd` accept it and ignore it. |
| `speed` | number | no | — (API default `1.0`) | Speaking-rate multiplier, finite, `0.25`–`4.0`. |
| `base_url` | non-empty string | no | — (SDK default `https://api.openai.com/v1`) | API base URL including the `/v1` path, for an OpenAI-compatible server or a proxy. Passed to `OpenAI(base_url=...)` only when set. |
| `sample_rate` | positive integer | no | `24000` | The rate of the server's `pcm` output. It **declares** the rate, it does not request one: the speech API has no rate parameter. OpenAI's own `pcm` is fixed at 24000, so a different value is accepted only together with `base_url`. |

### API key resolution

Identical to ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `OpenAI(api_key=...)`; the SDK's own `OPENAI_API_KEY` fallback is never relied on.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `voice`, `model` — non-empty strings (`model` defaults when absent).
- `instructions`, `base_url` — if present, non-empty strings.
- `speed` — if present, a finite number (not bool) in `[0.25, 4.0]`.
- `sample_rate` — if present, a positive integer (not bool); a value other than `24000` without `base_url` raises `ConfigError`.

`instructions` and `speed` are **omitted from the request when unset**, so the server's own defaults apply. The voice list is not checked locally: it differs per model and per compatible server, so a wrong `voice` or `model` fails at the first synthesis as `TTSError` (OpenAI answers 400 listing the supported voices / 404 `model_not_found`).

### Text

Text is sent verbatim, one request per `synthesize_text` call. A blank text (empty or whitespace only) makes no request and feeds nothing: the API rejects an empty `input` with 400. The endpoint caps `input` at 4096 characters; a longer text fails as `TTSError` (see "Open questions").

## Implementation notes

### SDK entry point

```python
from openai import OpenAI

client = OpenAI(api_key=key)  # + base_url=... when set
with client.audio.speech.with_streaming_response.create(
    input=text,
    model=self._model,
    voice=self._voice,
    response_format="pcm",
    # instructions=..., speed=... only when set
) as response:  # leaving the block closes the HTTP response
    for chunk in response.iter_bytes():
        ...
```

`with_streaming_response.create(...)` returns a context manager around a `StreamedBinaryAPIResponse`; the HTTP status is checked on entry (an error status raises `openai.APIStatusError` subclasses there), and `iter_bytes()` yields the chunked body as it arrives. The plain `create(...)` reads the whole body before returning and is not used. `stream_format` is left unset: for `pcm` OpenAI streams raw audio by default, and compatible servers may not know the parameter. One client is built in `__init__` and reused across utterances; the engine's `say` lock means at most one request runs at a time.

### Output format and sample rate

`response_format="pcm"` is headerless **signed 16-bit little-endian, mono, 24000 Hz** (`content-type: audio/pcm`, chunked transfer) — confirmed live against `gpt-4o-mini-tts` and `tts-1`: read little-endian the samples form a smooth waveform, byte-swapped they are noise. It is the contract's encoding as is, so the module does no conversion. `sample_rate` is the configured value (24000 by default).

Chunk boundaries are arbitrary byte counts — `gpt-4o-mini-tts` sends odd sizes such as 165 and 1369 bytes — so a sample is routinely split across two chunks. The module **carries the odd trailing byte** over to the next chunk; an incomplete sample left at the end of the body is dropped.

### Feeding in slices

Chunk sizes depend on the server: OpenAI's are small (~30 ms for `gpt-4o-mini-tts`, up to ~0.35 s for `tts-1`), but a compatible server may send a whole utterance in one chunk. `AudioPlayer.feed` blocks until the whole chunk is written, so the module feeds each chunk in **slices of at most 100 ms** (`sample_rate // 10` samples) and polls `stop` between slices — the Mistral rule, so a cancel is honored within one slice's playback whatever the server's chunking.

### OpenAI-compatible servers

The module speaks the plain OpenAI request and needs no knowledge of the server behind `base_url`. Two things vary and are the user's to set:

- **The output rate.** OpenAI's is 24 kHz; others differ (SGLang's Fish Audio Drama 3 is 44.1 kHz). `sample_rate` declares it so the player opens at the right rate.
- **Whether `pcm` is honored.** Some servers return a whole WAV file whatever the requested format. The module checks the first bytes of each response: a body that starts with `RIFF` raises `TTSError("OpenAI-compatible server returned WAV, not raw pcm")` before anything is fed, instead of playing the header as a click and the rest at a guessed rate. Whether a server *streams* (chunks as it synthesizes) or answers only once synthesis ends is invisible to the module; the latter just delays the first audio.

### Threading and cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"), exactly the Mistral shape: the worker opens the streaming response, polls `stop` between chunks and between slices, and leaves the `with` block on every path — normal end, `stop`, error — which closes the HTTP response so the server drops the request. `synthesize_text` re-raises `CancelledError` only once the thread has exited.

### Text stream (grouped)

`synthesize_text_stream` runs one worker through `run_text_stream_worker`, groups the pieces with `segment_sentences`, and makes one request per sentence inside that worker, feeding each sentence's audio before requesting the next — the pocket/mistral structure ([tts-module-interface.md](../tts-module-interface.md), "Local-model modules", last bullet). Audio for sentence one plays while the stream is still delivering sentence two.

Why not native: `/v1/audio/speech` takes one `input` per request. OpenAI's Realtime API (`gpt-realtime-*` over WebSocket) also answers one `response.create` per complete text, with no incremental text append, so it would be grouped too (see "Open questions").

### Dependencies and lazy import

Requires the `openai` extra: `pip install tts-engine[openai]` / `uv sync --extra openai`, declared in `[project.optional-dependencies]` as `openai = ["openai>=3.37,<4"]` and added to the `all` aggregate. `<4`: a Stainless/Castiron-generated SDK whose majors move client surfaces; the module relies on 3.x's `with_streaming_response`. The SDK is pure Python (`httpx2`, `pydantic`, `jiter`, `anyio`): a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `openai/module.py` must **not** import `openai` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    from openai import OpenAI
except ImportError as exc:
    raise ConfigError(
        "The 'openai' module requires the openai extra: pip install tts-engine[openai]"
    ) from exc
```

The backend package is named `tts_engine.modules.openai`; with absolute imports (the project default) `from openai import OpenAI` inside it still resolves to the SDK, not to the package itself.

`tests/modules/test_lazy_imports.py` adds `openai` to the poisoned modules and the `openai` row to its construction loop.

### Error handling

- Any exception raised by the SDK or the transport — an `openai.APIStatusError` (`AuthenticationError` 401 for a bad key, `BadRequestError` 400 for an unknown voice, an out-of-range `speed` or an over-long input, `NotFoundError` 404 `model_not_found`), an `APIConnectionError` / `APITimeoutError`, or a read failure mid-body — is caught and re-raised as `TTSError(f"OpenAI request failed: {exc}")`, chained via `raise ... from exc`. The SDK's own retries (2 by default, on connection errors, 408/409/429/5xx) happen before anything is fed and are left at the SDK default.
- A WAV body from a compatible server raises `TTSError` (above).
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as OpenAI request failures: the chunk iterator is advanced inside the provider `try`, `callback` is invoked outside it.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

## Module ID

Registered in `REGISTRY` as `"openai"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_openai.py`)** — `openai` is **faked** in `sys.modules` (a stub `OpenAI` whose `audio.speech.with_streaming_response.create` records its kwargs and returns a scripted context-managed response with `iter_bytes()` and a `closed` flag), so no request is ever made and the fast tier runs without the extra. Covers: `ConfigError` on a missing extra, on a missing/empty key and on each invalid field (parametrized), including a non-24 kHz `sample_rate` without `base_url`; key resolution (`api_key_env`, literal precedence, unset variable); `base_url` reaching the client only when set; `sample_rate` default and override; the request kwargs (`response_format="pcm"`, `model` default, `voice`, `instructions` and `speed` only when set); chunks reaching the callback unchanged, an odd byte carried over to the next chunk and a trailing half sample dropped; a 2 s chunk fed in 100 ms slices; a blank text making no request; a `RIFF` body raising `TTSError` before any feed; SDK errors on entry and mid-body wrapped in `TTSError` with the response closed, callback errors propagating unchanged; cancellation closing the response before `CancelledError`; and for `synthesize_text_stream`: one request per sentence, the first sentence's audio reaching the callback before the source has yielded its last piece, and cancellation with the source still open.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`openai`, `api_key_env: "OPENAI_API_KEY"`, voice `marin`, default model) plus `_REQUIRED_IMPORT["openai"] = "openai"` in `support.py`, so every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is absent ([testing.md](../testing.md), "Live tier"). CI maps an optional `OPENAI_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `openai==3.37.0`

Confirmed from the package and live calls on 2026-10-26:

- `from openai import OpenAI`; `OpenAI(api_key=..., base_url=..., ...)`; an empty or absent key with no `OPENAI_API_KEY` raises `OpenAIError("Missing credentials ...")` at construction, even with a `base_url`.
- `client.audio.speech.create(*, input, model, voice, instructions, response_format, speed, stream_format, ...)`, `response_format` ∈ `mp3`, `opus`, `aac`, `flac`, `wav`, `pcm`; `SpeechModel` = `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, `gpt-4o-mini-tts-2025-12-15`. `with_streaming_response.create(...)` returns a context manager yielding `StreamedBinaryAPIResponse` (`iter_bytes()`).
- `pcm` = s16le mono 24 kHz, `content-type: audio/pcm`, chunked. `gpt-4o-mini-tts` chunks are mostly 1369 or 4229 bytes (odd sizes); `tts-1` sends 1920 then 9600-byte chunks.
- Time to first audio: `tts-1` steady at ~1.2–1.3 s; `tts-1-hd` ~3 s; `gpt-4o-mini-tts` and its dated snapshot **erratic**, from 0.9 s to 19 s on the same short sentence, once 108 s total for 5.6 s of audio (well below real time).
- `base_url` + `sample_rate` exercised through the real SDK against a local stub server: the request reaches `<base_url>/audio/speech` with `{model, input, voice, response_format: "pcm"}` and `Authorization: Bearer <placeholder>`; the declared 44100 Hz reaches the engine; a `RIFF` body raises `TTSError` with nothing fed. No real compatible server was run.
- Unknown voice → 400 `BadRequestError` listing the 13 voices; unknown model → 404 `NotFoundError` `model_not_found`; `speed: 9` → 400; empty `input` → 400; bad key → 401 `AuthenticationError`. `instructions` with `tts-1` is accepted (and ignored).

## Open questions

Deferrals only (design is settled):

1. **The Realtime successor.** OpenAI has announced the shutdown of its `audio.speech` models (2027-01-06, per [the backend analysis](../../analysis/tts-backend-candidates.md)); the successor is a `gpt-realtime-*` model over the Realtime WebSocket — async, one `response.create` per complete text. That is a separate module (or a `transport` switch here) once the date is close; `/v1/audio/speech` stays useful for compatible servers either way.
2. **Erratic `gpt-4o-mini-tts` latency.** Up to ~19 s to first audio was observed. If it persists, consider defaulting to `tts-1` (steady ~1.2 s, no `instructions`) or exposing a request `timeout`; measure over the live tier first.
3. **Prefetching the next sentence.** As for Mistral and the local models: sentence N+1's request starts only once sentence N is fed, which adds one time-to-first-audio of silence between sentences.
4. **Inputs over 4096 characters.** `synthesize_text` could split a long text with `segment_sentences` and request each part; today it fails as `TTSError`. The engine's callers speak short texts.
5. **Custom voices** (`voice: {"id": "voice_…"}`, created from audio samples) and **`stream_format: "sse"`**. Each is a config field away; neither is needed to speak.
