# Module-declared sample rate

**Status:** Done

Implements the settled behavior in `specs/tts-module-interface.md` ("Audio format contract"), `specs/audio-player.md` ("Audio format"), and `specs/architecture.md` ("`TTSEngine` construction"): move the PCM sample rate from a fixed 44100 Hz constant to a per-module property the engine reads and hands to `AudioPlayer`. Encoding (signed 16-bit) and channels (mono) stay fixed. This is the prerequisite for any local-model module with a non-44100 native rate; it adds no new module by itself.

## Scope

- `src/tts_engine/modules/base.py` — add an abstract `sample_rate` property to `TTSModule`.
- `src/tts_engine/modules/elevenlabs.py` — implement `sample_rate` returning `44100` (its `mp3_44100_128` output).
- `src/tts_engine/audio.py` — `AudioPlayer.__init__` gains `sample_rate: int = 44100`; the `OutputStream` opens at it instead of the module-level `_SAMPLERATE` constant.
- `src/tts_engine/engine.py` — pass `sample_rate=self._module.sample_rate` when constructing `AudioPlayer`.
- `tests/modules/test_tts_module_interface.py` — a fake/subclass now must supply `sample_rate`; assert it's part of the contract.
- `tests/test_audio.py` — assert the `OutputStream` opens at the passed `sample_rate` (e.g. patch `sd.OutputStream`, feed a chunk, check the `samplerate` kwarg).
- `tests/test_engine.py` — assert the engine reads `module.sample_rate` and constructs `AudioPlayer` with it (the fakes patched into `engine.py` already stand in for module + player).

## Steps

1. In `base.py`, add:
   ```python
   @property
   @abstractmethod
   def sample_rate(self) -> int: ...
   ```
   with the docstring from the spec. This makes `sample_rate` mandatory for every `TTSModule`.
2. In `elevenlabs.py`, implement the property returning `44100`. Keep the existing `output_format="mp3_44100_128"` — the value must stay consistent with what miniaudio decodes to (already `sample_rate=44100`). No behavior change.
3. In `audio.py`, add the `sample_rate` parameter (default `44100`), store it, and use `self._sample_rate` in the `sd.OutputStream(...)` call. Keep `_CHANNELS`/`_DTYPE` constants; drop or keep `_SAMPLERATE` as the default only.
4. In `engine.py`, change the player construction to
   `AudioPlayer(device=config.player.device, sample_rate=self._module.sample_rate)`.
   Order: build the module first (already the case), then read its rate.
5. Update the tests in scope: give any test double a `sample_rate` (44100 is fine), and add the two assertions (player opens at the given rate; engine forwards the module's rate).
6. Flip the three specs from `Updated` back to `Implemented` and mirror in `specs/_index.md`; mark this plan `Done` in `plans/_index.md`.

## Verification

- `uv run pytest` — the new/updated assertions plus the full existing suite (including `tests/test_project_map.py`, which stays green: no files added or moved).
- `uv run ruff check .` and `uv run ruff format .`.
- `uv run pyright` — the abstract property must type-check against `ElevenLabsModule` and any test doubles.
- Optionally, an e2e smoke (`zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e'`) to confirm ElevenLabs playback is unchanged at 44100 Hz.

Mark `Done` only once lint, type-check, and tests pass, and the three governing specs are back to `Implemented`.
