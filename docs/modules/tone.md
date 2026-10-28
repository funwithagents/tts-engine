# `tone`

**Kind:** `fixture` · **Synthesis:** Sine tone, no synthesis · **Install:** Base installation · **Credentials:** None · **Voice listing:** —

A fixture module for tests and demos, not a TTS backend. For any text it plays a sine tone whose length is proportional to the text length. It needs no key, no extra, no model, and no files, and it is deterministic. That makes it a good module for an integration test suite, or for checking audio output.

```json
{ "module": { "type": "tone" } }
```

| Field | Required | Default | Description |
|---|---|---|---|
| `sample_rate` | no | `24000` | Output rate in Hz. |
| `frequency` | no | `440.0` | Sine frequency in Hz. |
| `seconds_per_char` | no | `0.05` | Tone duration is `len(text) * seconds_per_char` seconds. |
| `amplitude` | no | `0.2` | Peak amplitude, in (0, 1]. |

## See also

- [specs/modules/tone.md](../../specs/modules/tone.md): the module's contract: every field's validation, the audio format, cancellation, errors and what it was validated against.
- [examples/config.tone.json](../../examples/config.tone.json): a complete MCP-server config.
