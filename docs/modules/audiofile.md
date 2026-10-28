# `audiofile`

**Kind:** `fixture` · **Synthesis:** Pre-recorded WAV files, no synthesis · **Install:** Base installation · **Credentials:** None · **Voice listing:** —

A fixture module for tests and demos, not a TTS backend. It maps known sentences to pre-recorded WAV files and plays the matching file, falling back to `default_file` when no entry matches:

```json
{
  "module": {
    "type": "audiofile",
    "default_file": "fixtures/default.wav",
    "files": [
      { "text": "Hello, world!", "file": "fixtures/hello.wav" },
      { "text": "Goodbye.", "file": "fixtures/goodbye.wav" }
    ]
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `files` | one of `files` or `default_file` | `[]` | List of `{"text", "file"}` entries. Matching is exact and case-sensitive after collapsing whitespace. |
| `default_file` | one of `files` or `default_file` | — | WAV played when no entry matches. Without it, an unmatched text raises a TTS error. |

Paths are absolute or relative to `base_dir` (the config file's directory when loaded from a file). Every file must be a 16-bit mono WAV, and all files must share one sample rate, which becomes the module's rate. Files are checked when the engine is constructed.

## See also

- [specs/modules/audiofile.md](../../specs/modules/audiofile.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.audiofile.json](../../examples/config.audiofile.json): a complete MCP-server config.
