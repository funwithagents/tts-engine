---
code:
  - src/tts_engine/modules/gemini/__init__.py
  - src/tts_engine/modules/gemini/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_gemini.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Gemini Module

**Status:** Implemented

## Overview

`gemini` implements `TTSModule` using Google's **Gemini TTS** models (`gemini-3.8-flash-tts` by default) through the official `google-genai` Python SDK and the Gemini Developer API. Gemini TTS is a generative model asked for an `AUDIO` response: the text is the prompt, a prebuilt voice is chosen in the speech config, and the audio streams back as headerless 16-bit PCM, so there is no codec and no conversion. It is one API-backed provider among others — not the default and not the base install — and ships behind the `gemini` packaging extra (see "Dependencies").

Its shape is the ElevenLabs/Mistral one: a synchronous SDK iterator (`generate_content_stream`) driven from the cancellable worker. For a text stream it **groups the pieces into sentences** (one request per sentence), like Mistral: `generateContent` takes one complete prompt per request, and Google's own guidance for long or incremental text is one request per sentence chunk (see "Text stream").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "gemini"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Gemini API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Google's own convention is `GEMINI_API_KEY`). Lets a config file be committed with no secret. |
| `voice` | non-empty string | no | — (API default) | A prebuilt voice name (`Kore`, `Puck`, `Charon`, `Zephyr`, …). Omitted, the request carries no voice config and the API picks its default voice. |
| `model` | non-empty string | no | `"gemini-3.8-flash-tts"` | Gemini TTS model id (`gemini-3.8-flash-lite-tts`, `gemini-2.5-pro-preview-tts`, …). |
| `language_code` | non-empty string | no | — (auto-detected) | BCP-47 code (`en-US`, `fr-FR`) pinning the spoken language. Omitted, the model detects the language from the text. |
| `timeout` | finite number > 0 (seconds) | no | `30` | Request bound passed to the client as `http_options.timeout` (milliseconds, rounded up): the wait for the response and for each read of the stream. A stalled request fails as `TTSError` after at most this long, which is also how long a cancel issued during the stall can take ([tts-module-interface.md](../tts-module-interface.md), "What cancellation guarantees"). |

### API key resolution

Identical to ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `genai.Client(api_key=...)`; the SDK's own `GEMINI_API_KEY` / `GOOGLE_API_KEY` environment fallback is never relied on.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `model` — non-empty string (defaults when absent).
- `voice`, `language_code` — if present, non-empty strings.
- `timeout` — if present, a finite number (not bool) `> 0`.

`voice` and `language_code` are **omitted from the request when unset**, so the API's own defaults apply and the module never hardcodes a copy of them. Whether a voice, model or language code exists is only known to the API: a wrong one fails at the first synthesis as `TTSError` (the API answers 400 "No matching speaker voice found" / 404 model "not found" / 400 "Invalid language_code").

### Text and steering

Text is sent verbatim as the prompt. Gemini TTS is steerable in natural language and with inline tags (a leading "Say cheerfully:", `[whispering]`); the module neither adds nor strips any of it. The flip side of a generative model is that text phrased as an instruction may be followed rather than read; that is the model's behaviour, not the module's.

## Implementation notes

### SDK entry point

```python
import google.genai as genai

client = genai.Client(api_key=key)
chunks = client.models.generate_content_stream(
    model=self._model,
    contents=text,
    config={
        "response_modalities": ["AUDIO"],
        "speech_config": {  # only the keys that were set
            "voice_config": {"prebuilt_voice_config": {"voice_name": voice}},
            "language_code": language_code,
        },
    },
)
for chunk in chunks:  # GenerateContentResponse
    for candidate in chunk.candidates or []:
        for part in (candidate.content.parts if candidate.content else None) or []:
            if part.inline_data and part.inline_data.data:
                pcm = part.inline_data.data  # bytes, already decoded by the SDK
```

The config is passed as a plain dict (the SDK accepts `GenerateContentConfigDict`), so the module never imports `google.genai.types`. `speech_config` is omitted entirely when neither `voice` nor `language_code` is set. `generate_content_stream` is a synchronous **lazy generator**: the HTTP request is sent on the first `next()`, so request errors (bad key, voice, model) surface there, not at the call. One client is built in `__init__` and reused across utterances; the engine's `say` lock means at most one request runs at a time.

### Output format and sample rate

Every audio part is `inline_data` with `mime_type` `audio/l16; rate=24000; channels=1`. Despite RFC 2586's network byte order for L16, the bytes are **signed 16-bit little-endian** — confirmed live (read as LE the waveform is smooth, mean |Δ| ≈ 900; read as BE it is noise, ≈ 17 000). That is already the contract's encoding ([tts-module-interface.md](../tts-module-interface.md), "Audio format contract"): the module feeds the bytes unchanged. `sample_rate` is the constant `24000`; `GenerateContentConfig` has no output-rate option (its `response_format` / `AudioResponseFormat.sample_rate` is rejected as an extra field by SDK 2.33).

The module checks each audio part's mime type and raises `TTSError` if it is not `audio/l16`, names a `rate=` other than 24000, or names a `channels=` other than 1 (a stereo response fed to a mono sink would play interleaved at half speed), so a future format change fails loudly instead of playing noise; a parameter the API omits is taken at its documented value (24 kHz, mono). The parts are fed through the shared `PCMFeeder` ([tts-module-interface.md](../tts-module-interface.md), "Shared PCM helpers"): a part whose byte length is odd (a sample split across two chunks — not observed, chunks are multiples of 1920 bytes) has its odd byte carried over to the next part, and a lone byte left at the end is dropped. Parts without `inline_data` (a text part) are skipped.

The feeder hands each part to `callback` in **slices of at most 100 ms** (2400 samples) and polls `stop` between slices, so a cancel is honored within one slice's playback whatever size the API makes its parts. The parts observed live are small (1920–7680 bytes, 40–160 ms), but the API documents no part size, and until the shared feeder landed the module fed them unsliced on the strength of that measurement; nothing rests on it now.

### End of stream and refusals

The stream ends when the generator is exhausted (the last chunk carries `finish_reason=STOP`). A candidate whose `finish_reason` is set to anything other than `STOP` (`SAFETY`, `PROHIBITED_CONTENT`, `OTHER`, `MAX_TOKENS`, …), or a chunk whose `prompt_feedback.block_reason` is set, raises `TTSError` naming the reason, after any audio already fed — the utterance was cut or refused and the caller should know. The SDK's automatic continuation (`CONTINUATION` hops) is internal to the generator and never seen by the module.

### Threading and cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"), the Mistral shape: the worker creates the generator, polls `stop` between chunks, and **closes the generator** on every path — normal end, `stop`, error — through the shared `cleanup` helper, so a failing close never replaces the utterance's own error. While the worker is blocked in `next(chunks)` waiting for the server, it cannot see `stop`: that wait is bounded by `timeout`. Closing it runs the SDK's response iteration to `GeneratorExit`; observed live, it returns at once and the pooled HTTP connection is dropped (the pool is empty afterwards), so the server stops sending. `synthesize_text` re-raises `CancelledError` only once the thread has exited.

### Text stream (grouped)

`synthesize_text_stream` runs one worker through `run_text_stream_worker`, groups the pieces with `segment_sentences`, and makes one `generate_content_stream` request per sentence inside that worker, feeding each sentence's audio before requesting the next — the pocket/phonon/mistral structure ([tts-module-interface.md](../tts-module-interface.md), "Local-model modules", last bullet).

Why not native: `generateContent` takes one complete prompt per request, and the SDK has no text-append call for it. The Live API (`client.aio.live`) does accept incremental text, but it answers as a conversational model rather than reading the text verbatim, and it is async-only. Google's TTS guidance for long text is one request per sentence chunk, which is what grouping does. Grouping has two costs, both in "Known limitations": a pause before each sentence, and one request per sentence against the per-minute quota.

### Known limitations

Measured live on 2026-10-26 (16 calls, Gemini Developer API, Tier 1 key). Both follow from the grouped shape: one request per sentence.

- **A pause before each sentence.** Time to first audio is 0.42–0.98 s, median ~0.8 s (`gemini-3.8-flash-tts`: 0.70 s for 2 words, 0.78 s for 12, 0.86 s for 35; `gemini-3.8-flash-lite-tts`: 0.87 s for 35). It is a fixed per-request cost: sentence length barely changes it, and the lite model starts no sooner (it only finishes sooner: 10 s of audio in 2.4 s against 3.2 s). Once audio flows there is no stall, since both models generate 3–4× faster than real time. In a text stream, the next sentence's request starts only once the current sentence is fed, so a reply is heard with a ~0.8 s silence between sentences; a complete text (`synthesize_text`) pays it once. Prefetching would hide it (see "Open questions").
- **The request quota caps sentences per minute.** Gemini API rate limits count requests per model per minute: **10 on Tier 1** (observed as `429 RESOURCE_EXHAUSTED`, quota `GenerateRequestsPerMinutePerProjectPerModel`, with a suggested retry delay of ~56 s). The module makes one request per sentence, so a text stream of more than ~10 sentences within a minute — a long LLM reply, or several `say` calls in a row — fails partway with `TTSError` carrying the 429. The module does not retry: waiting out the delay mid-utterance would be worse than failing. Remedies, none in v1: a higher usage tier on the Google account; larger groups per request (see "Open questions"); spreading load over the flash and lite models, which have separate quotas.

### Dependencies and lazy import

Requires the `gemini` extra: `pip install tts-engine[gemini]` / `uv sync --extra gemini`, declared in `[project.optional-dependencies]` as `gemini = ["google-genai>=2.33,<3"]` and added to the `all` aggregate. `<3`: the SDK is pre-generated from the API surface and moves fast; the module relies on the 2.x `generate_content_stream` and response shapes. Pure Python (`httpx`, `pydantic`, `google-auth`, `websockets`), no codec: a light extra.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `gemini/module.py` must **not** import `google.genai` at file top. The import happens inside `__init__`, before any config field is read:

```python
try:
    import google.genai as genai
except ImportError as exc:
    raise ConfigError(
        "The 'gemini' module requires the gemini extra: pip install tts-engine[gemini]"
    ) from exc
```

The `import google.genai as genai` form is deliberate. `from google import genai` does not raise when `sys.modules["google.genai"]` is `None` (CPython's `IMPORT_FROM` falls back to that entry and binds `None`), so a poisoned or half-installed package would get past the guard and fail later with an `AttributeError`.

`tests/modules/test_lazy_imports.py` adds `google.genai` to the poisoned modules and the `gemini` row to its construction loop.

### Error handling

- Any exception raised by the SDK or the transport — a `google.genai.errors.APIError` (`ClientError`: bad key, unknown voice, model or language; `ServerError`: 5xx), an `httpx` connection or read failure mid-stream — is caught and re-raised as `TTSError(f"Gemini request failed: {exc}")`, chained via `raise ... from exc`. A wrong mime type, a non-`STOP` finish reason or a block reason raises `TTSError` too (see above).
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as Gemini request failures: the generator is advanced inside the provider `try`, `callback` is invoked outside it.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.

### Lifetime

Built in `__init__`, released by `TTSModule.close()` when the engine closes ([tts-module-interface.md](../tts-module-interface.md), "Lifetime and ownership"): `close()` calls `genai.Client.close()`, releasing the SDK's HTTP pool; every response generator is per utterance.

## Module ID

Registered in `REGISTRY` as `"gemini"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_gemini.py`)** — `google.genai` is **faked** in `sys.modules` (a stub `genai.Client` whose `models.generate_content_stream` records its kwargs and returns a scripted generator with a `closed` flag), so no request is ever made and the fast tier runs without the extra. Covers: `ConfigError` on a missing extra, on a missing/empty key and on each invalid field (parametrized); key resolution (`api_key_env`, literal precedence, unset variable); the request kwargs (`model` default, `contents`, `response_modalities`, `speech_config` present only with the keys that were set); audio parts reaching the callback unchanged, text parts skipped; an odd-length part carried over; a wrong mime type, rate or channel count as `TTSError`, and a mime with or without its parameters accepted when they are mono 24 kHz; a non-`STOP` finish reason and a block reason as `TTSError`; SDK errors on the first `next()` and mid-stream wrapped in `TTSError` with the generator closed, callback errors propagating unchanged, and a callback error surviving a failing generator close; `timeout` reaching the client in milliseconds (default and set) and invalid values rejected; cancellation closing the generator before `CancelledError`; and for `synthesize_text_stream`: one request per sentence, the first sentence's audio reaching the callback before the source has yielded its last piece, and cancellation with the source still open.
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`gemini`, `api_key_env: "GEMINI_API_KEY"`, voice `Kore`) plus `_REQUIRED_IMPORT["gemini"] = "google.genai"` in `support.py`, so every per-module scenario runs against the real API and **skips cleanly** when the key or the extra is absent ([testing.md](../testing.md), "Live tier"). CI maps an optional `GEMINI_API_KEY` secret ([ci.md](../ci.md)).

## Validated against `google-genai==2.33.0`

Confirmed from the package and live calls on 2026-10-26:

- `import google.genai as genai`; `genai.Client(api_key=...)`. `client.models.generate_content_stream(*, model, contents, config)` is a sync generator function; the request is sent on the first `next()`.
- TTS models listed by `client.models.list()`: `gemini-3.8-flash-tts`, `gemini-3.8-flash-lite-tts`, `gemini-3.1-flash-tts-preview`, `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts`.
- `gemini-3.8-flash-tts`: first audio 0.57–1.1 s (median ~0.8 s, see "Known limitations"); 4.9 s of audio fully received after 1.7 s (well ahead of real time); parts of 1920–7680 bytes; `mime_type` `audio/l16; rate=24000; channels=1`; little-endian int16; last chunk `finish_reason=STOP`. `gemini-3.8-flash-lite-tts`: first audio ~1.0 s, then bursty.
- No voice config → valid audio in the API's default voice. `language_code="fr-FR"` → valid.
- Unknown voice → `ClientError` 400 INVALID_ARGUMENT "No matching speaker voice found"; unknown model → `ClientError` 404 NOT_FOUND; `language_code="xx-YY"` → `ClientError` 400 "Invalid language_code". All raised on the first `next()`.
- `generator.close()` after two chunks returns in under 1 ms, and the client's `httpx` connection pool is empty afterwards.
- `GenerateContentConfig(response_format=[...])` → pydantic `extra_forbidden`: no output-rate option.

## Open questions

Deferrals only (design is settled):

1. **Prefetching the next sentence.** In the grouped text stream, sentence N+1's request starts only once sentence N is fed, which adds one time-to-first-audio (~0.8 s median for Gemini, the largest of the grouped API backends) of silence between sentences. A worker could request N+1 while N plays, since the API runs well ahead of real time; that is a second thread and a bounded queue. Mistral, pocket and phonon share the gap and the deferral, so it belongs in a shared helper rather than in one module.
2. **Larger groups per request.** Grouping two or three sentences, or up to a minimum character count, per request would divide the request count against the per-minute quota (see "Known limitations"), at the cost of a later first audio. Needs a grouping knob on `segment_sentences` or a Gemini-side merge.
3. **Vertex AI.** `genai.Client(vertexai=True, project=..., location=...)` reaches the same models through Google Cloud with ADC credentials instead of an API key. A `vertexai` / `project` / `location` field set away; not needed to speak.
4. **Multi-speaker and style fields.** `multi_speaker_voice_config` (two named speakers in one prompt) and a configured style prefix prepended to every request. Both are prompt-shape features; the second would make each grouped sentence carry the same direction.
5. **Cloud TTS Chirp 3 HD** (`google-cloud-texttospeech`, `streaming_synthesize`) is a different API and SDK with native text input; if wanted it is its own module, not a mode of this one.
