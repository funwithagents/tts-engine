---
code:
  - src/tts_engine/modules/__init__.py
  - src/tts_engine/modules/audiofile.py
tests:
  - tests/modules/test_audiofile.py
  - tests-e2e/test_modules.py
---

# Audio File Module

**Status:** Implemented

## Purpose

`audiofile` is a **fixture module**: a `TTSModule` that synthesizes nothing. It maps input texts to pre-recorded WAV files and plays the matching file, falling back to a default file when no entry matches. It exists so a demo or a test suite can hear real, meaningful audio for known sentences with **no API key, no packaging extra, and no network** — deterministic and always installed.

It is **not** a text-to-speech backend and the docs must never present it as one: real speech comes from a provider module installed through an extra ([project.md](project.md), "Dependency strategy for TTS backends"). The other fixture module, [tone-module.md](tone-module.md), needs no files at all and is the better default for automated tests.

## Config fields

All fields go under the `engine.module` block alongside `"type": "audiofile"` (see [configuration.md](configuration.md)).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `files` | list of `{"text": string, "file": string}` objects | no | `[]` | The text → file associations. `text` is the exact sentence to match (punctuation, spaces, and all); `file` is a WAV path, absolute or relative to `base_dir`. |
| `default_file` | string | no | — | WAV path played when the text matches no entry. Absolute or relative to `base_dir`. |
| `base_dir` | string | no | config file's directory, else the working directory | The reserved module key every module shares: the directory relative paths resolve against. Filled in by the config loaders — see [configuration.md](configuration.md), "`engine.module` block". |

`files` is a **list of objects**, not a JSON object keyed by text, so a long sentence with punctuation reads naturally and duplicates are an explicit validation error rather than a silently overwritten key.

Example:

```json
{
  "type": "audiofile",
  "default_file": "fixtures/default.wav",
  "files": [
    { "text": "Hello, world!", "file": "fixtures/hello.wav" },
    { "text": "Goodbye.", "file": "fixtures/goodbye.wav" }
  ]
}
```

### Validation

Validated at construction, raising `ConfigError` (the message names the offending entry or file):

- `files` — if present, a list; every item an object with a non-empty string `text` and a non-empty string `file`. Two entries whose normalized `text` (see "Matching") is equal are a duplicate → `ConfigError`.
- `default_file` — if present, a non-empty string.
- At least one of `files` (non-empty) or `default_file` must be given; a module with nothing to play is a `ConfigError`.
- Every referenced path is resolved with `resolve_path(config, value)` from `modules/base.py` ([tts-module-interface.md](tts-module-interface.md), "Path resolution") and its WAV header is read at construction with the stdlib `wave` module. Each file must exist, be readable as WAV, have **2-byte samples** (signed 16-bit) and **1 channel**; and **all files must share one frame rate**. Any violation is a `ConfigError` naming the file. This is a header read only; audio data is read at stream time.

## Behavior

- **`sample_rate`** returns the common frame rate of the configured files. Fixed for the module's lifetime, per [tts-module-interface.md](tts-module-interface.md).
- **Matching.** Text is normalized by collapsing every run of whitespace to a single space and stripping both ends; comparison is then **exact and case-sensitive** against the normalized entry texts. The first match wins (duplicates are already rejected). No match → `default_file`. No match and no `default_file` → `TTSError("no audio file configured for text: ...")`, raised **before** any callback.
- **`stream(text, options, callback)`** opens the chosen file with `wave` inside a `run_cancellable_worker` worker, reads it in chunks of `4096` frames (`readframes(4096)`, 8192 bytes for 16-bit mono), and passes each chunk to `callback` until the file is exhausted, polling the stop flag between chunks (cancellation per the module contract). There is **no pacing** (no sleep). Files are read on every call, never cached in memory.
- **Deterministic.** The same text always produces the same bytes; a test can predict the byte count from the file's frame count.
- **Errors.** A file that fails to open or read at stream time (deleted or truncated since construction) raises `TTSError` naming the file. Exceptions raised by `callback` propagate unchanged, never relabeled as `TTSError`.

## Module ID

Registered in `REGISTRY` as `"audiofile"` ([modules/__init__.py](../src/tts_engine/modules/__init__.py)). Lives in the base install; it depends on nothing beyond the standard library.

## Testing

- **Unit (`tests/modules/test_audiofile.py`)** — WAV fixtures are generated in `tmp_path` with the `wave` module (a few hundred frames of silence or a ramp), never committed. Covers: `ConfigError` for a missing file, a stereo or 8-bit file, mismatched rates between two files, a duplicate `text`, a malformed `files` entry, and an empty config; `sample_rate` equals the files' rate; an exact match plays that file's bytes (predicted from the fixture); whitespace-normalized text still matches; an unmatched text plays `default_file`; an unmatched text with no default raises `TTSError` with no callback invoked; relative paths resolve against `base_dir`; a callback exception propagates unchanged.
- **Live (`tests-e2e/`)** — one `MODULES` row (`audiofile`) whose config points `base_dir` at `tests-e2e/fixtures/` with two small committed WAV files, so both per-module scenarios run through the real sink and audio hardware (see [testing.md](testing.md)). It never skips.

## Open questions

Deferrals only (design is settled):

1. **Other formats / stereo / resampling.** Only 16-bit mono WAV at one common rate is accepted. Decoding MP3 or downmixing stereo would need `miniaudio` or `numpy` work; not worth it for a fixture module. Revisit if a demo needs it.
