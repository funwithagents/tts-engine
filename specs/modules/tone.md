---
code:
  - src/tts_engine/modules/__init__.py
  - src/tts_engine/modules/tone/__init__.py
  - src/tts_engine/modules/tone/module.py
tests:
  - tests/modules/test_tone.py
  - tests-e2e/test_modules.py
---

# Tone Module

**Status:** Implemented

## Purpose

`tone` is a **fixture module**: a `TTSModule` that synthesizes nothing. For any text it emits a pure sine tone whose duration is proportional to the text length. It exists so the engine, the tools layer, the MCP transport, and any downstream integration suite can run **end to end with no API key, no packaging extra, no model download, and no fixture files** — deterministic, instant, and always installed.

It is the module the live tier's module-agnostic tests drive by default (see [testing.md](../testing.md)), and the natural choice for a downstream project's own test suite. It is **not** a text-to-speech backend and the docs must never present it as one: real speech comes from a provider module installed through an extra ([project.md](../project.md), "Dependency strategy for TTS backends").

The second fixture module, [audiofile-module.md](audiofile.md), plays pre-recorded files instead and is the right choice when a demo needs to sound like speech.

## Config fields

All fields go under the `engine.module` block alongside `"type": "tone"` (see [configuration.md](../configuration.md)). Every field is optional; the module works with `{"type": "tone"}` alone.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `sample_rate` | integer > 0 | no | `24000` | The rate (Hz) the module declares and generates at. |
| `frequency` | number > 0 | no | `440.0` | Sine frequency in Hz. |
| `seconds_per_char` | number > 0 | no | `0.05` | Tone duration is `len(text) * seconds_per_char` seconds. With the default, a 20-character text plays for one second. |
| `amplitude` | number in (0, 1] | no | `0.2` | Peak amplitude as a fraction of full scale, so the tone is audible but not loud. |

### Validation

Validated at construction, raising `ConfigError`:

- `sample_rate` — if present, an integer `> 0`; booleans are rejected rather than treated as integers.
- `frequency` and `seconds_per_char` — if present, a number (int or float, not bool) `> 0`.
- `amplitude` — if present, a number (not bool) with `0 < amplitude <= 1`.

## Behavior

- **`sample_rate`** returns the configured (or default) rate. It is fixed for the module's lifetime, per [tts-module-interface.md](../tts-module-interface.md).
- **`stream(text, options, callback)`** computes `total_frames = round(len(text) * seconds_per_char * sample_rate)` and emits a sine wave of that many frames as signed 16-bit little-endian mono PCM, generated with `numpy` (already a base dependency): `sample = round(amplitude * 32767 * sin(2π · frequency · n / sample_rate))` for frame index `n`, starting at `n = 0` so the wave is phase-continuous across chunks.
- **Chunking.** Frames are emitted in chunks of `4096` frames (8192 bytes); the last chunk is shorter. The loop runs inside `run_cancellable_worker` and polls the stop flag between chunks, so cancellation obeys the module contract ("Cancellation" in [tts-module-interface.md](../tts-module-interface.md)). There is **no pacing** (no sleep): chunks are pushed as fast as the sink accepts them, exactly like a provider module.
- **Deterministic.** The same config and text always produce the same bytes. The output length in bytes is `2 * total_frames`, which a test can predict exactly.
- **Errors.** Nothing at stream time can fail except the callback; exceptions raised by `callback` propagate unchanged (they are downstream playback failures, never relabeled as `TTSError`). Invalid config fails at construction with `ConfigError`.

## Module ID

Registered in `REGISTRY` as `"tone"` ([modules/__init__.py](../../src/tts_engine/modules/__init__.py)). Lives in the base install; it depends on nothing beyond `numpy`.

## Testing

- **Unit (`tests/modules/test_tone.py`)** — `ConfigError` for each invalid field; default `sample_rate` is 24000 and a configured one is reported; `stream` into a capture callback yields exactly `2 * round(len(text) * seconds_per_char * sample_rate)` bytes, in whole int16 samples, in more than one chunk for a long text; the same text twice yields identical bytes; a callback exception propagates unchanged; cancellation stops the stream before `stream()` raises `CancelledError`.
- **Live (`tests-e2e/`)** — one `MODULES` row (`tone`, no gate: it never skips) so both per-module scenarios run; and it is the `default_module()` the MCP transport test drives, so `test_mcp.py` runs on every machine with audio hardware (see [testing.md](../testing.md)).

## Open questions

None currently.
