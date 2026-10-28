---
code:
  - src/tts_engine/modules/openai_compatible/__init__.py
  - src/tts_engine/modules/openai_compatible/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_openai_compatible.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# OpenAI-compatible Module

**Status:** Implemented

## Overview

`openai-compatible` implements `TTSModule` with the `POST /v1/audio/speech` endpoint through the official `openai` Python SDK: OpenAI's own speech endpoint (models `gpt-4o-mini-tts` — its `2025-03-20` snapshot by default — `tts-1`, `tts-1-hd`), and a de-facto standard many other servers implement. It asks for raw `pcm` and reads the chunked HTTP body as it arrives, so there is no codec to decode and no conversion: the bytes are already the contract's signed 16-bit little-endian mono. It is one API-backed provider among others — not the default and not the base install — and ships behind the `openai-compatible` packaging extra (see "Dependencies").

With `base_url` (and `sample_rate`) it reaches any **OpenAI-compatible** `/v1/audio/speech` server: vLLM-Omni, SGLang Omni, `mlx_audio.server`, Kokoro-FastAPI and the like. (`pocket-tts serve` is not one: it serves its own `/tts` route only.) Such a server must honor `response_format="pcm"`; one that answers with a WAV file is rejected with a clear `TTSError` (see "OpenAI-compatible servers").

Its shape is the Mistral one: a synchronous SDK iterator driven from the cancellable worker. For a text stream it **groups the pieces into sentences** (one request per sentence): the endpoint takes one complete `input` per request (see "Text stream").

**Why the name.** OpenAI shuts down every model this endpoint serves on 2027-01-06 (`tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` and its snapshots); its successor, `gpt-realtime-2.1-mini`, is served only by the Realtime API, which is a different protocol (a long-lived WebSocket and a conversation model that speaks a response). The endpoint outlives OpenAI's models on it through the compatible servers above, so the module is named for the endpoint, and the vendor key `openai` belongs to the Realtime module, [openai.md](openai.md) — following the rule in [project.md](../project.md) ("Dependency strategy for TTS backends"): a module follows the provider's current API and keeps an older one only while it serves a distinct use case. The module was registered as `openai` until 2026-10-27, before anyone depended on the key.

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "openai-compatible"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | API key, given literally. A keyless compatible server still needs one (the SDK refuses to build a client without), so give it any placeholder such as `"unused"`. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (OpenAI's own convention is `OPENAI_API_KEY`). Lets a config file be committed with no secret. |
| `voice` | non-empty string | yes | — | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin`, `cedar` (OpenAI recommends `marin` or `cedar` with `gpt-4o-mini-tts`; `tts-1` and `tts-1-hd` reject those two with a 400 listing their nine), or whatever names a compatible server uses. The API has no default voice. |
| `model` | non-empty string | no | `"gpt-4o-mini-tts-2025-03-20"` | Speech model id: `gpt-4o-mini-tts` (the alias, resolving to `gpt-4o-mini-tts-2025-12-15`), a dated snapshot, `tts-1`, `tts-1-hd`, or a compatible server's model. The default is the older snapshot because it answers steadily where the alias and the newer snapshot stall (open question 2); it keeps `instructions` and every voice, `marin` and `cedar` included. |
| `instructions` | non-empty string | no | — | Free-text voice direction (tone, pace, accent, emotion). Honored by `gpt-4o-mini-tts`; `tts-1`/`tts-1-hd` accept it and ignore it. |
| `speed` | number | no | — (API default `1.0`) | Speaking-rate multiplier, finite, `0.25`–`4.0`. |
| `base_url` | non-empty string | no | — (SDK default `https://api.openai.com/v1`) | API base URL including the `/v1` path, for an OpenAI-compatible server or a proxy. Passed to `OpenAI(base_url=...)` only when set. The hosted default serves no model after 2027-01-06; the field becomes required then (open question 1). |
| `sample_rate` | positive integer | no | `24000` | The rate of the server's `pcm` output. It **declares** the rate, it does not request one: the speech API has no rate parameter. OpenAI's own `pcm` is fixed at 24000, so a different value is accepted only together with `base_url`. |
| `timeout` | finite number > 0 (seconds) | no | `30` | Per-request bound passed as the SDK's `timeout`: the wait for the response and for each read of the body, instead of the SDK's 600 s default. A stalled request fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). The `gpt-4o-mini-tts` alias has been measured stalling for up to 28 s before its first byte (open question 2); raise it if those stalls must be waited out. |

### API key resolution

Identical to ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `OpenAI(api_key=...)`; the SDK's own `OPENAI_API_KEY` fallback is never relied on.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `voice`, `model` — non-empty strings (`model` defaults when absent).
- `instructions`, `base_url` — if present, non-empty strings.
- `speed` — if present, a finite number (not bool) in `[0.25, 4.0]`.
- `timeout` — if present, a finite number (not bool) `> 0`.
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

Chunk boundaries are arbitrary byte counts — `gpt-4o-mini-tts` sends odd sizes such as 165 and 1369 bytes — so a sample is routinely split across two chunks. The shared `PCMFeeder` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers") **carries the odd trailing byte** over to the next chunk; an incomplete sample left at the end of the body is dropped.

### Feeding in slices

Chunk sizes depend on the server: OpenAI's are small (~30 ms for `gpt-4o-mini-tts`, up to ~0.35 s for `tts-1`), but a compatible server may send a whole utterance in one chunk. `AudioPlayer.feed` blocks until the whole chunk is written, so the feeder hands each chunk to `callback` in **slices of at most 100 ms** (`slice_bytes_for(sample_rate)`) and polls `stop` between slices — the rule every byte-stream provider shares, so a cancel is honored within one slice's playback whatever the server's chunking.

### OpenAI-compatible servers

The module speaks the plain OpenAI request and needs no knowledge of the server behind `base_url`. Two things vary and are the user's to set:

- **The output rate.** OpenAI's is 24 kHz; others differ (SGLang's Fish Audio Drama 3 is 44.1 kHz). `sample_rate` declares it so the player opens at the right rate.
- **Whether `pcm` is honored.** Some servers return a whole WAV file whatever the requested format. The module checks the first bytes of each response: a body that starts with `RIFF` raises `TTSError("OpenAI-compatible server returned WAV, not raw pcm")` before anything is fed, instead of playing the header as a click and the rest at a guessed rate. Whether a server *streams* (chunks as it synthesizes) or answers only once synthesis ends is invisible to the module; the latter just delays the first audio.

### Threading and cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"), exactly the Mistral shape: the worker opens the streaming response, polls `stop` between chunks and between slices, and closes the HTTP response on every path — normal end, `stop`, error — through the shared `cleanup` helper, so the server drops the request and a failing close never replaces the utterance's own error. `synthesize_text` re-raises `CancelledError` only once the thread has exited. While the worker is blocked in `next(chunks)` waiting for the server (the request sent, no byte yet), it cannot see `stop`: that wait is bounded by `timeout`, not by the slice size.

### Text stream (grouped)

`synthesize_text_stream` runs one worker through `run_text_stream_worker`, groups the pieces with `segment_sentences`, and makes one request per sentence inside that worker, feeding each sentence's audio before requesting the next — the pocket/mistral structure ([tts-module-interface.md](../tts-module-interface.md), "Local-model modules", last bullet). Audio for sentence one plays while the stream is still delivering sentence two.

Why not native: `/v1/audio/speech` takes one `input` per request. (OpenAI's Realtime API, the [`openai`](openai.md) module, also answers one `response.create` per complete text with no incremental text append, so it groups too.)

### Dependencies and lazy import

Requires the `openai-compatible` extra: `pip install tts-engine[openai-compatible]` / `uv sync --extra openai-compatible`, declared in `[project.optional-dependencies]` as `openai-compatible = ["openai>=3.37,<4"]` and added to the `all` aggregate. The `openai` (Realtime) extra pulls the same SDK, with its `realtime` extra. `<4`: a Stainless/Castiron-generated SDK whose majors move client surfaces; the module relies on 3.x's `with_streaming_response`. The SDK is pure Python (`httpx2`, `pydantic`, `jiter`, `anyio`): a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `openai_compatible/module.py` must **not** import `openai` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    from openai import OpenAI
except ImportError as exc:
    raise ConfigError(
        "The 'openai-compatible' module requires the openai-compatible extra: "
        "pip install tts-engine[openai-compatible]"
    ) from exc
```

The backend package is `tts_engine.modules.openai_compatible` (a registry key may hold a hyphen, a Python package may not); with absolute imports (the project default) `from openai import OpenAI` inside it resolves to the SDK.

`tests/modules/test_lazy_imports.py` adds `openai` to the poisoned modules and the `openai-compatible` row to its construction loop.

### Error handling

- Any exception raised by the SDK or the transport — an `openai.APIStatusError` (`AuthenticationError` 401 for a bad key, `BadRequestError` 400 for an unknown voice, an out-of-range `speed` or an over-long input, `NotFoundError` 404 `model_not_found`), an `APIConnectionError` / `APITimeoutError`, or a read failure mid-body — is caught and re-raised as `TTSError(f"OpenAI-compatible request failed: {exc}")`, chained via `raise ... from exc`. The SDK's own retries (2 by default, on connection errors, 408/409/429/5xx) happen before anything is fed and are left at the SDK default.
- A WAV body from a compatible server raises `TTSError` (above).
- A response that stalls longer than `timeout` is an `APITimeoutError`, hence `TTSError`.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as request failures: the chunk iterator is advanced inside the provider `try`, `callback` is invoked outside it.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` calls `OpenAI.close()`, releasing the SDK's HTTP pool; every streaming response is per utterance.

## Module ID

Registered in `REGISTRY` as `"openai-compatible"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_openai_compatible.py`)** — `openai` is **faked** in `sys.modules` (a stub `OpenAI` whose `audio.speech.with_streaming_response.create` records its kwargs and returns a scripted context-managed response with `iter_bytes()` and a `closed` flag), so no request is ever made and the fast tier runs without the extra. Covers: `ConfigError` on a missing extra, on a missing/empty key and on each invalid field (parametrized), including a non-24 kHz `sample_rate` without `base_url`; key resolution (`api_key_env`, literal precedence, unset variable); `base_url` reaching the client only when set; `sample_rate` default and override; the request kwargs (`response_format="pcm"`, `model` default, `voice`, `instructions` and `speed` only when set); chunks reaching the callback unchanged, an odd byte carried over to the next chunk and a trailing half sample dropped; a 2 s chunk fed in 100 ms slices; a blank text making no request; a `RIFF` body raising `TTSError` before any feed; SDK errors on entry and mid-body wrapped in `TTSError` with the response closed, callback errors propagating unchanged, and a callback error surviving a failing response close; `timeout` reaching the request (default and set) and invalid values rejected; cancellation closing the response before `CancelledError`; and for `synthesize_text_stream`: one request per sentence, the first sentence's audio reaching the callback before the source has yielded its last piece, and cancellation with the source still open.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`openai-compatible`, `api_key_env: "OPENAI_API_KEY"`, the default model `gpt-4o-mini-tts-2025-03-20` pinned explicitly so a default change cannot silently alter the live tier's timing, voice `marin`, against OpenAI's hosted endpoint; see open question 2) plus `_REQUIRED_IMPORT["openai-compatible"] = "openai"` in `support.py`, so every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is absent ([testing.md](../testing.md), "Live tier"). CI maps an optional `OPENAI_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `openai==3.37.0`

Confirmed from the package and live calls on 2026-10-26:

- `from openai import OpenAI`; `OpenAI(api_key=..., base_url=..., ...)`; an empty or absent key with no `OPENAI_API_KEY` raises `OpenAIError("Missing credentials ...")` at construction, even with a `base_url`.
- `client.audio.speech.create(*, input, model, voice, instructions, response_format, speed, stream_format, ...)`, `response_format` ∈ `mp3`, `opus`, `aac`, `flac`, `wav`, `pcm`; `SpeechModel` = `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, `gpt-4o-mini-tts-2025-12-15`. `with_streaming_response.create(...)` returns a context manager yielding `StreamedBinaryAPIResponse` (`iter_bytes()`).
- `pcm` = s16le mono 24 kHz, `content-type: audio/pcm`, chunked. `gpt-4o-mini-tts` chunks are mostly 1369 or 4229 bytes (odd sizes); `tts-1` sends 1920 then 9600-byte chunks.
- Time to first audio: `tts-1` steady at ~1.2–1.3 s; `tts-1-hd` ~3 s; `gpt-4o-mini-tts` and its dated snapshot **erratic**, from 0.9 s to 19 s on the same short sentence, once 108 s total for 5.6 s of audio (well below real time).
- `base_url` + `sample_rate` exercised through the real SDK against a local stub server: the request reaches `<base_url>/audio/speech` with `{model, input, voice, response_format: "pcm"}` and `Authorization: Bearer <placeholder>`; the declared 44100 Hz reaches the engine; a `RIFF` body raises `TTSError` with nothing fed. No real compatible server was run.
- Unknown voice → 400 `BadRequestError` listing the 13 voices; unknown model → 404 `NotFoundError` `model_not_found`; `speed: 9` → 400; empty `input` → 400; bad key → 401 `AuthenticationError`. `instructions` with `tts-1` is accepted (and ignored).

## Voice listing

`list_voices` raises `VoiceListingUnsupported`: `/v1/audio/speech` has no endpoint that lists voices. Without `base_url` the message points to OpenAI's voices (https://platform.openai.com/docs/guides/text-to-speech). With one, it says that the server at that URL documents its own voices, since a compatible server's voice names are its own.

## Open questions

Deferrals only (design is settled):

1. **After OpenAI's shutdown (2027-01-06).** Decided: the Realtime successor is a separate module, `openai`, not a `transport` switch here (the two share the vendor and the SDK and nothing else; see "Why the name"). Deferred to that date: the hosted default goes — `base_url` becomes required (one validation rule), the `gpt-4o-mini-tts-2025-03-20` model default is dropped (a compatible server names its own models), `config.openai-compatible.json` points at a local server, and the live row moves off the hosted snapshot to a local OpenAI-compatible stub or skips. Until then the hosted default still works and the live row still exercises the module against a real server with no local setup.
2. **Erratic `gpt-4o-mini-tts` latency.** Measured on 2026-10-26 with the raw SDK (retries off, no module code) on one short sentence, four runs each: the `gpt-4o-mini-tts` alias took 1.2, 1.3, 13.8 and 28.5 s to first byte and up to 116 s in total for 4 s of audio; `gpt-4o-mini-tts-2025-12-15` (what the alias resolves to) 1 to 12 s to first byte and up to 54 s total; `gpt-4o-mini-tts-2025-03-20` 0.35 to 0.44 s and under 1 s total; `tts-1` 0.9 to 2.1 s and under 3 s total. The module adds nothing to this — it is the provider, and others report the same (OpenAI community thread "TTS Api is very slow and giving timeout", May to September 2026, where OpenAI support could not reproduce it; a project issue logging random 20 to 80× slowdowns and 30 s stalls since 2026-09-27). The live row therefore pins `tts-1` so the CI live tier's time is bounded. Decided 2026-10-28: the module default is `gpt-4o-mini-tts-2025-03-20`, which keeps `instructions` and every voice (`marin` and `cedar` included) and answered in 0.33–0.70 s on a re-measure that day — when the alias happened to be fast too (0.44–0.70 s), so the stalls come and go. Confirmed 2026-10-28 by the TTFA benchmark at 20 runs per cell, all three `gpt-4o-mini-tts` ids within one hour: `2025-03-20` at a 353 ms median, 461 ms p90 and 504 ms maximum over 80 runs with no stall; `2025-12-15` 504 / 596 / 960 ms; the alias 501 / 604 / 3712 ms (two runs over a second); `tts-1` 1.1–1.4 s mean, and with curl its first byte at 0.8–1.35 s only 0.3 s before the last, so it synthesizes nearly the whole clip before sending. The live row therefore moved from `tts-1` to the `2025-03-20` snapshot (pinned explicitly, voice `marin`) the same day: three times faster with a bounded worst case, and the model the module ships by default.
3. **Prefetching the next sentence.** As for Mistral and the local models: sentence N+1's request starts only once sentence N is fed, which adds one time-to-first-audio of silence between sentences.
4. **Inputs over 4096 characters.** `synthesize_text` could split a long text with `segment_sentences` and request each part; today it fails as `TTSError`. The engine's callers speak short texts.
5. **Custom voices** (`voice: {"id": "voice_…"}`, created from audio samples) and **`stream_format: "sse"`**. Each is a config field away; neither is needed to speak.
