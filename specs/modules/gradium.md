---
code:
  - src/tts_engine/modules/gradium/__init__.py
  - src/tts_engine/modules/gradium/module.py
  - src/tts_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_gradium.py
  - tests/modules/test_lazy_imports.py
  - tests-e2e/test_modules.py
---

# Gradium Module

**Status:** Implemented

## Overview

`gradium` implements `TTSModule` using the [Gradium](https://gradium.ai) streaming TTS API through the official `gradium` Python SDK. It requests **raw PCM** from the API (signed 16-bit mono, at the module's declared rate), so unlike ElevenLabs there is no codec and no decoding step — chunks go from the WebSocket to `callback` as-is. It is one API-backed provider among others — not the default and not the base install — and ships behind the `gradium` packaging extra (see "Dependencies").

What sets it apart from the other providers is the SDK's shape: it is **async-only** (aiohttp WebSocket), where the ElevenLabs SDK is a synchronous iterator and pocket-tts a synchronous generator. This spec fixes the third module shape — an **async-SDK provider** — for how such a backend meets the threading and cancellation rules of [tts-module-interface.md](../tts-module-interface.md) (see "Threading model").

## Config fields

All fields go under the `engine.module` block in `config.json` alongside `"type": "gradium"` (see [configuration.md](../configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `api_key` | non-empty string | one of `api_key`/`api_key_env` | — | Gradium API key, given literally. |
| `api_key_env` | non-empty string | one of `api_key`/`api_key_env` | — | Name of an environment variable holding the API key (Gradium's own convention is `GRADIUM_API_KEY`). Lets a config file be committed with no secret. |
| `voice_id` | non-empty string | yes | — | The Gradium voice id used for synthesis (a 16-character id such as `91EdXxJDbWICDBgz`, "Alex", from the flagship catalog, or a custom voice's uid). |
| `model` | non-empty string | no | `"default"` | Gradium model alias, sent as `model_name`: `"default"` (the standard model) or `"gradium-tts-beta"` (the newest beta). |
| `sample_rate` | integer | no | `48000` | Output PCM rate. One of `8000`, `16000`, `24000`, `44100`, `48000` — the rates Gradium offers as `pcm_<rate>` output formats. `48000` is the model's native rate; any other value is resampled server-side. |
| `temp` | number | no | — (API default 0.7) | Sampling temperature, `0.0`–`1.4`; `0.0` is deterministic, higher is more varied. |
| `cfg_coef` | number | no | — (API default 2.0) | Voice similarity, `1.0`–`4.0`; higher stays closer to the target voice, very high can introduce artifacts. |
| `padding_bonus` | number | no | — (API default 0.0) | Speech speed, `-4.0`–`4.0`; negative is faster, positive slower. |
| `rewrite_rules` | non-empty string | no | — (API default: the voice's language) | Text-rewriting rules applied before synthesis (a language alias, or `"none"` to disable). |
| `pronunciation_id` | non-empty string | no | — | Id of a Gradium pronunciation dictionary to apply. |
| `base_url` | non-empty string | no | — (SDK default `https://api.gradium.ai/api/`) | API base URL, for a regional or self-hosted endpoint. Passed to `GradiumClient(base_url=...)` only when set. |

### API key resolution

Identical to ElevenLabs ([elevenlabs-module.md](elevenlabs.md), "API key resolution"): a non-empty literal `api_key` wins; otherwise, if `api_key_env` is set, the key is read from that environment variable. Non-string values raise `ConfigError`. If `api_key_env` names a variable that is unset or empty, construction raises `ConfigError` identifying the variable. If neither yields a key, construction raises `ConfigError`. The resolved key is always passed explicitly to `GradiumClient(api_key=...)`: the SDK's own fallback to `GRADIUM_API_KEY` is never relied on, so the module's key rules are the only ones in force and a config that names no key fails here, not inside the SDK.

### Validation

Validated at construction, raising `ConfigError` before the SDK client is built:

- `voice_id`, `model` — non-empty strings (`model` defaults when absent).
- `sample_rate` — if present, an integer in `{8000, 16000, 24000, 44100, 48000}`; booleans are rejected rather than treated as integers.
- `temp`, `cfg_coef`, `padding_bonus` — if present, numeric (not bool) and within the inclusive range given in the table.
- `rewrite_rules`, `pronunciation_id`, `base_url` — if present, non-empty strings.

The four voice-setting fields are **omitted from the request when unset**, so the API's own defaults apply and the module never hardcodes a copy of them; the table's "API default" column is informational.

## Implementation notes

### SDK entry point

Use the official `gradium` SDK's **realtime** interface, `client.tts_realtime(...)` (`gradium.stream.Tts`), not the `tts_stream()` convenience. Both stream audio, but `Tts` is an async context manager whose `__aexit__` closes the WebSocket and session immediately, which is what cancellation needs (below); `tts_stream()`'s teardown instead awaits its receive task until the *server* has finished the whole synthesis, so a cancel mid-utterance would block for the remainder of the audio.

The per-utterance flow, all inside one connection:

```python
async with self._client.tts_realtime(
    wait_for_ready_on_start=True,
    model_name=self._model,
    voice_id=self._voice_id,
    output_format=f"pcm_{self._sample_rate}",
    json_config=self._json_config,  # only the fields that were set; omitted when empty
    pronunciation_id=...,  # only when set
) as tts:
    await tts.send_text(text)
    await tts.send_eos()
    async for (
        msg
    ) in tts:  # dicts: "audio" (bytes, base64 already decoded), "text", "end_of_stream"
        if stop.is_set():
            break
        if msg["type"] == "audio":
            callback(msg["audio"])
```

The setup is sent and the server's `ready` message awaited on entry (`wait_for_ready_on_start=True`), so a rejected setup (bad voice id, bad key) fails before any text is sent. The `ready` message carries the negotiated `sample_rate`; the module checks it against its declared `sample_rate` and raises `TTSError` on a mismatch — a cheap guard that the audio contract's "declared rate" actually holds for the stream about to be fed.

### Output format and sample rate

`output_format` is always `pcm_<sample_rate>` (e.g. `pcm_48000`), which Gradium serves as signed 16-bit little-endian mono PCM at exactly that rate, in 80 ms chunks. This satisfies the audio format contract ([tts-module-interface.md](../tts-module-interface.md)) with **no conversion in the module**: no decode (unlike ElevenLabs's MP3) and no float → int16 (unlike pocket). `sample_rate` returns the configured value — declared, fixed for the module's lifetime, and read once by the engine to open the player.

### Threading model (async-SDK provider)

`AudioPlayer.feed` blocks (sounddevice's `write` waits for buffer room), so `callback` must never run on the engine's event loop — that would stall every other coroutine for the duration of playback. The synchronous providers avoid this because `run_cancellable_worker` runs their SDK loop in a thread. An async SDK gets the same treatment: the worker thread runs a **private event loop** of its own.

```python
async def stream(self, text, options, callback):
    def _blocking_stream(stop: threading.Event) -> None:
        asyncio.run(self._consume(text, callback, stop))

    await run_cancellable_worker(_blocking_stream)
```

`_consume` is the `async with ... tts_realtime(...)` flow above; `callback` is invoked from the worker thread (calls never overlap, since there is one consumer loop), and the engine's loop stays free. Nothing else in the module touches the engine loop. The cost — a second event loop per utterance — is negligible next to the network round trip, and it keeps the module inside the one cancellation helper every module uses.

### Cancellation

Cooperative via `run_cancellable_worker` ([tts-module-interface.md](../tts-module-interface.md), "Cancellation"): the consumer polls `stop` between messages and `break`s out of the `async for` when it is set. Leaving the `async with` block then runs `Tts.__aexit__`, which closes the WebSocket and the aiohttp session at once — the server drops the request, and `asyncio.run` returns as soon as that completes, so the thread exits promptly and the coroutine re-raises `CancelledError` only after callbacks have stopped.

### Dependencies and lazy import

Requires the `gradium` extra: `pip install tts-engine[gradium]` / `uv sync --extra gradium`, declared in `[project.optional-dependencies]` as `gradium = ["gradium>=0.6"]` and added to the `all` aggregate (which reaches the `dev` group for free — [project.md](../project.md), "Key dependencies"). The SDK is pure Python and pulls in only `aiohttp` (plus `numpy`, already a base dependency): a light extra, the same order as ElevenLabs's.

`modules/__init__.py` imports every module *class* eagerly to build `REGISTRY`, so `gradium/module.py` must **not** import `gradium` at file top. The import happens inside `__init__`, turning a missing extra into an actionable `ConfigError`:

```python
try:
    from gradium import GradiumClient
except ImportError as exc:
    raise ConfigError(
        "The 'gradium' module requires the gradium extra: pip install tts-engine[gradium]"
    ) from exc
```

`tests/modules/test_lazy_imports.py` adds `gradium` (and `aiohttp`) to the poisoned modules and the `gradium` row to its construction loop.

### Error handling

- Any exception raised by the SDK or the connection — a server `error` message (the SDK raises `RuntimeError`), an `aiohttp` connection/handshake failure, an unexpected first message — is caught and re-raised as `TTSError(f"Gradium request failed: {exc}")`, chained via `raise ... from exc`. The `ready` sample-rate mismatch is a `TTSError` of its own.
- Exceptions raised by `callback` are downstream playback failures. They propagate unchanged and are not mislabeled as Gradium request failures: `callback` is invoked outside the `try` that wraps the SDK calls, the same discipline as the other providers.
- A missing extra surfaces as `ConfigError` at construction, not `TTSError`.
- Cancellation requests cooperative worker shutdown and waits until callback activity has stopped before propagating.

## Module ID

Registered in `REGISTRY` as `"gradium"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)).

## Testing

- **Unit (`tests/modules/test_gradium.py`)** — the `gradium` SDK is **faked** (a stub `GradiumClient` whose `tts_realtime` returns a scripted async context manager / async iterator), so no socket is ever opened and the fast tier runs without the extra. Covers: `ConfigError` on a missing extra, on a missing/empty key, and on each invalid field (parametrized); key resolution (`api_key_env`, literal precedence, unset variable); `sample_rate` reflecting the config; the setup kwargs reaching the SDK (`output_format=pcm_<rate>`, `model_name`, `voice_id`, `json_config` present only when a voice setting is set, `pronunciation_id`/`base_url` only when set); `stream` feeding audio bytes to the callback unchanged and skipping non-audio messages; the `ready` rate mismatch raising `TTSError`; SDK errors wrapped in `TTSError` while callback errors propagate unchanged; and cancellation stopping callbacks before `stream()` raises. Follows the ElevenLabs unit-test shape (mock the library, assert observable behavior via the arguments sent to the client).
- **Live (`tests-e2e/test_modules.py`)** — one `MODULES` row (`gradium`, `api_key_env: "GRADIUM_API_KEY"`, the Alex voice) plus `_REQUIRED_IMPORT["gradium"] = "gradium"` in `support.py`, so both per-module scenarios run against the real API and **skip cleanly** when the key or the extra is absent (see [testing.md](../testing.md), "Live tier").

## Validated against `gradium==0.6.4`

The SDK surface this spec relies on, confirmed from the pinned package:

- `GradiumClient(*, base_url="https://api.gradium.ai/api/", api_key=None, ...)`; `api_key=None` falls back to `GRADIUM_API_KEY` (not relied on here).
- `client.tts_realtime(**setup) -> gradium.stream.Tts`, an async context manager + async iterator; `Tts(wait_for_ready_on_start=True)` awaits the `ready` message on entry and exposes it as `tts.ready` (a dict with `sample_rate`, `request_id`, `frame_size`, …); `send_text(str)`, `send_eos()`; iteration yields dicts whose `"audio"` value is already base64-decoded `bytes`; a server `{"type": "error"}` raises `RuntimeError`; `__aexit__` closes the socket and session.
- `TTSSetup` fields: `model_name`, `voice_id`, `output_format`, `json_config` (dict, JSON-encoded by the SDK), `pronunciation_id`.
- TTS `output_format` values and rates: `pcm` (48000), `pcm_8000`, `pcm_16000`, `pcm_24000`, `pcm_44100`, `pcm_48000`; all 16-bit signed mono, 80 ms chunks.
- `json_config` voice settings: `temp` (0–1.4, default 0.7), `cfg_coef` (1–4, default 2.0), `padding_bonus` (−4–4, default 0.0), `rewrite_rules` (string).

## Open questions

Deferrals only (design is settled):

1. **Incremental text.** The realtime interface accepts text word-by-word (`send_text` repeatedly before `send_eos`), which an LLM-streaming caller could exploit. `stream()` takes a complete string today, so the module sends it in one message; revisit if `TTSOptions`/the engine ever grow a streaming-text input.
2. **Connection reuse.** One WebSocket per utterance is the simplest correct shape and matches how the other providers issue one request per `stream()`. Keeping a connection warm across utterances (Gradium supports `close_ws_on_eos: false`) would shave the handshake off time-to-first-audio; measure before adding it.
