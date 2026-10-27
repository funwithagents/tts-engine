# ElevenLabs v3/v4 models over the text-to-dialogue socket

**Status:** Done

Implements [specs/modules/elevenlabs.md](../specs/modules/elevenlabs.md) ("Two sockets", the `model` row, "Text stream"), following item 6 of `analysis/api-currency-audit.md` and the rule in [specs/project.md](../specs/project.md) that a module follows the provider's current API. ElevenLabs serves its v3 and v4 models (`eleven_v3`, `eleven_v3_conversational`, `eleven_v4`, `eleven_v4_turbo` — the flagship real-time model, ~100 ms) on a different real-time socket than the v2 family: the TTS stream-input socket rejects a v3/v4 model at the handshake, and the text-to-dialogue stream-input socket rejects a v2 model. Today a config with `model: eleven_v3` works for `say()` and fails on the first `say_stream()`. After this plan every model works on both entry points: `synthesize_text` keeps one path (HTTP `text_to_speech.stream`, which serves the whole catalog), and `synthesize_text_stream` picks the socket from the model prefix, ElevenLabs' own rule (`model_id` "must start with `eleven_v3` or `eleven_v4`" for the dialogue socket). Leaves out: routing `say()` through the dialogue HTTP endpoint (its 2,000-character request cap would force `say()` to split; the TTS endpoint takes every model in one request), multi-voice dialogue, `flush` mid-stream, `audio_effects`, PCM output.

## Scope

- `src/tts_engine/modules/elevenlabs/module.py` — one sender/receiver loop over a small protocol record (`_SocketProtocol`: URL, opening frame, text frame, end frame, final key, keep-alive frame) chosen at construction from the model prefix; the dialogue variant registers the voice in the opening frame, sends text as `inputs`, ends with `close_socket`, reads `is_final`, and the receiver sends `keep_alive` after 10 s without a client frame (the server's fixed 20 s inactivity limit); informational frames without `audio` are ignored.
- `tests/modules/test_elevenlabs.py` — the fake socket speaks both protocols; the wire test parametrized over both; keep-alive sent on the dialogue socket during a pause and never on the TTS socket; a dialogue-only frame ignored; a v4 model still reaches `text_to_speech.stream` for a complete text.
- `tests-e2e/support.py` — an `elevenlabs-v4` row on `eleven_v4_turbo`, same key.
- `examples/config.elevenlabs-v4.json` — a complete v4 config next to `config.elevenlabs.json`.
- `specs/modules/elevenlabs.md` — status `Implemented` → `Updated` while this plan runs, back to `Implemented` with it; the "Two sockets" section, the `model` row, the stream section rewritten around the protocol record; the probe record.
- `specs/_index.md`, `specs/testing.md`, `specs/ci.md`, `README.md` — the second socket and the second live row.

## Steps

1. Probe the live APIs (scratch scripts, not committed) for what the audit left open: which endpoint accepts which model (HTTP TTS takes every model, v3/v4 included; the TTS socket answers HTTP 400 to v3/v4; the dialogue socket answers HTTP 403 to a v2 model; the dialogue HTTP endpoint answers `model_does_not_support_dialogue` to a v2 model), and the dialogue socket's behaviour: audio flows before `close_socket` once ~40 characters are buffered, `keep_alive` every 10 s carries a 25 s pause, `close_socket` with no text answers `is_final` at once, a `settings` frame in the opening message is accepted, an invalid voice closes the socket without a close frame, and an `is_final_audio_for_turn` frame precedes `is_final`.
2. Module: pick the protocol from the model prefix in `__init__`; move the URL and frames out of `_stream_input_chunks` into the record; add the idle keep-alive to the receiver's poll.
3. Tests: extend the fake, parametrize the wire test, add the keep-alive and v4-HTTP cases; check that wiring v4 to the TTS socket makes the new tests fail.
4. Live row and example; spec and docs.

## Verification

`uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest`; live `-k elevenlabs` (both rows) via `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e -k elevenlabs'`; an engine-level `say_stream` on `eleven_v4_turbo` with a paused source longer than 20 s. Mark this plan `Done` (here and in [_index.md](_index.md)) and the spec `Implemented` only once all pass.

## Result

Unit tier 630 cases (4 new); with the prefix table emptied (every model on the TTS socket) the three dialogue cases fail and the TTS keep-alive case still passes. Live `-k elevenlabs` 10/10 on both rows. Engine-level `say_stream` on `eleven_v4_turbo` with a 25 s pause between two pieces: 8.0 s of audio in 25.7 s, first audio at 0.42 s, the socket kept through the pause.
