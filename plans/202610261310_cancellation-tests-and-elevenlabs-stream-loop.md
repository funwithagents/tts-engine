# Cancellation tests and the ElevenLabs stream-input loop

**Status:** Done

Done note (2026-10-26): unit tier 285 passed in ~2 s; live tier 31 passed. Cancel surfaced after 40–70 ms on every backend for both `say` and `say_stream` (tone 0.04/0.05 s, audiofile 0.04/0.04 s, pocket 0.05/0.05 s, phonon 0.05/0.05 s, elevenlabs 0.04/0.07 s, gradium 0.06/0.06 s). Before the ElevenLabs change, the `say_stream` cancel scenario never got its first chunk with the source paused: the SDK path received nothing until the next piece.

Closes the cancellation-coverage gap found after plan `202610261140_streaming-text-input`: cancellation was tested for `synthesize_text` on every module but audiofile, and for `synthesize_text_stream` only while the source was idle (tone and the bridge). Adds mid-audio cancellation tests for the text-stream path on every module, audiofile tests for both paths, engine tests that cancel `say` and `say_stream` on a real module, and two live scenarios. Writing the live scenarios surfaced a defect in the ElevenLabs text-stream path, fixed here per [specs/modules/elevenlabs.md](../specs/modules/elevenlabs.md) ("Text stream"): the SDK's `convert_realtime` receives no audio while the text source pauses, so playback stalled and a cancel hung; the module now runs its own sender/receiver loop over the stream-input WebSocket. Deliberately leaves the PCM-output deferral in place.

## Scope

- `src/tts_engine/modules/base.py` — `split_at_last_space`, the word-boundary helper both incremental providers share (moved out of the Gradium module).
- `src/tts_engine/modules/gradium/module.py` — import the shared helper.
- `src/tts_engine/modules/elevenlabs/module.py` — `_stream_input_chunks`: the own loop over `websockets.sync` (sender thread + polling receiver); `_decode_and_feed` closes its raw source in `finally`; `convert_realtime` no longer used.
- `tests/modules/test_elevenlabs.py` — scripted stream-input socket fake replacing the `convert_realtime` fake; mid-audio and silent-server cancellation tests.
- `tests/modules/test_tone.py`, `test_audiofile.py`, `test_pocket.py`, `test_gradium.py`, `test_phonon.py` — mid-audio cancellation of `synthesize_text_stream` with the source still open; audiofile also for `synthesize_text`.
- `tests/test_engine.py` — `task.cancel()` on `say` and `say_stream` through a real tone module and a paced capture sink: no feed after the cancel surfaces, one drain.
- `tests-e2e/test_modules.py` — `test_module_say_cancel_stops_feeding`, `test_module_say_stream_cancel_stops_feeding`, logging how long each backend's cancel took.
- `specs/modules/elevenlabs.md`, `specs/tts-module-interface.md`, `specs/testing.md`, the module specs' "Testing" bullets, `specs/_index.md`, `plans/_index.md`.

## Steps

1. **Unit cancellation tests, every module.** The shared shape: a source that yields one complete unit (a sentence for the grouping modules, a word for the incremental ones) then blocks forever; a backend fake that produces audio endlessly for it; a counting callback; `task.cancel()` after the first chunk; `CancelledError`; the count unchanged 50 ms later; the provider connection closed and no end-of-stream sent.
2. **Engine and live tier.** As in Scope. The live sink sleeps 50 ms per chunk so the utterance outlasts the cancel; the stream source yields a ≥50-character first sentence (ElevenLabs's generation floor) then blocks.
3. **ElevenLabs own loop.** `_stream_input_chunks(pieces, stop)`: connect with the `xi-api-key` header, send the opening message (voice settings, `chunk_length_schedule=[50]`), start a daemon sender thread (word groups via `split_at_last_space`, remainder, `{"text": ""}`; nothing after `stop`), receive with `recv(timeout=0.1)` polling `stop` and the sender's error, yield decoded `audio`, stop on `isFinal` or a normal close. Fed to the shared `_decode_and_feed`.
4. **Docs and statuses** as in Scope; this plan `Done` once everything below passes.

## Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright` — clean.
- `uv run pytest` — passes within the 5-second budget.
- `uv run pytest tests-e2e -k cancel` with the keys sourced — every backend's two cancel scenarios pass; the logged cancel times are recorded in this plan's Done note.
- `uv run pytest tests-e2e -k elevenlabs` — the conformance scenarios still pass on the new loop.
