# Discard player

**Status:** Done

Implements `engine.player.discard` as settled in `specs/configuration.md` ("`engine.player` block → `PlayerConfig`") and `specs/audio-player.md` ("`DiscardPlayer`"): a config flag that makes the engine play on no device, dropping the audio at real-time pace. It adds no per-call option and leaves `AudioPlayer` unchanged.

## Scope

- `src/tts_engine/config.py` — `PlayerConfig.discard: bool = False`; the loader rejects a non-boolean `discard` and `discard: true` with a non-null `device`
- `src/tts_engine/audio.py` — `DiscardPlayer` (an `AudioSink`) and `DISCARD_BUFFER_SECONDS`
- `src/tts_engine/engine.py` — the default sink is a `DiscardPlayer` when the config discards (`_default_sink`); the MCP server and the CLIs build their engine through the same constructor
- `tests/test_config.py` — discard accepted, defaulted, rejected (with a device, non-boolean)
- `tests/test_audio.py` — the pace: an utterance in small chunks takes its duration, `drain` waits out the remainder, the next utterance is paced afresh
- `tests/test_no_audio_import.py` — an engine built from a discard config speaks at real-time pace with `sounddevice` unimportable
- `specs/configuration.md`, `specs/audio-player.md`, `specs/architecture.md`, `specs/audio-sink.md`, `specs/_index.md` — the field, the sink, the engine's choice
- `docs/configuration.md`, `docs/audio-sinks.md`, `docs/troubleshooting.md` — the field for users

## Steps

1. Add `discard` to `PlayerConfig` and its validation to `TTSEngineConfig.from_dict`.
2. Add `DiscardPlayer`: `feed` advances one running deadline by the chunk's duration and sleeps until it is at most `DISCARD_BUFFER_SECONDS` ahead; `drain` sleeps until the deadline and resets it.
3. Have `TTSEngine.__init__` build it, instead of `AudioPlayer`, when the config discards and no sink is injected.
4. Tests, specs, docs as listed.

## Verification

`uv run ruff check .`, `uv run ruff format src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` all pass; then this plan goes `Done` and both specs return to `Implemented` (here and in the indexes).
