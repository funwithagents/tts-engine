# A `threads` field on the local modules

**Status:** Done

Implements the `threads` field of [specs/modules/phonon.md](../specs/modules/phonon.md), [kokoro.md](../specs/modules/kokoro.md) and [piper.md](../specs/modules/piper.md) ("Config fields", "Validation", "Construction", "Device"), the `positive_int` helper of [tts-module-interface.md](../specs/tts-module-interface.md) ("Shared config helpers"), and the `threads` tag of [benchmark.md](../specs/benchmark.md). A local model takes every core by default; the field caps the threads one synthesis can keep busy, so a four-core board can leave room for the rest of its software, and the benchmark can measure what the cap costs. It deliberately leaves out pocket (its library pins torch to one thread itself) and qwen3 (its binding exposes no thread count), each saying so in its spec, and the GPU side of the question.

## Scope

- `src/tts_engine/modules/base.py` — `positive_int(config, field, *, label)`: absent or `null` → `None`; an `int` ≥ 1 passes; a bool, float or string → `ConfigError`.
- `src/tts_engine/modules/phonon/module.py` — `threads` → `ptts.set_num_threads` before the load (a rejection → `ConfigError`); a module-level memory of the count the process's first model loaded with, and a warning when a later module's count differs, since `ptts` sizes its pool once.
- `src/tts_engine/modules/kokoro/module.py` — with `threads`, the session built by the module (`intra_op_num_threads`, the library's `resolve_providers`) and `Kokoro.from_session`; without, the constructor as before.
- `src/tts_engine/modules/piper/module.py` — with `threads`, what `PiperVoice.load` does on a session sized to `threads` (`PiperConfig.from_dict` of the JSON, the CPU provider, `PiperVoice(config=, session=)`); without, `load` as before.
- `tests/modules/test_tts_module_interface.py`, `test_phonon.py`, `test_kokoro.py`, `test_piper.py` — the helper; invalid values; the setter's order and the warning (phonon); the session's options, providers and the constructor path (kokoro, piper), with a fake `onnxruntime`.
- `specs/modules/{phonon,kokoro,piper,pocket,qwen3}.md`, `specs/tts-module-interface.md`, `specs/benchmark.md`, `specs/_index.md` — the field, its validation, construction and device notes, the measured cost, the helper, the tag; why pocket and qwen3 have no field.
- `docs/modules/{phonon,kokoro,piper}.md` — the option row.
- `benchmarks/matrix.json`, `benchmarks/README.md` — twelve `+t2` / `+t4` entries tagged `threads` (phonon 31m `cpu` and `cpu+q8_0`, kokoro fp32 and int8, piper ljspeech-medium and lessac-low) and their README rows.

## Steps

1. `positive_int` and its tests.
2. The three modules and their tests; a real-library check of each at `threads=2` against unset (CPU percent of a core and wall time on one sentence), and of phonon's process-wide behaviour (a second model's count cannot apply).
3. Specs, index rows, docs pages, matrix entries and README rows, this plan.
4. Lint, type check, fast tier; `bench.py list --tags threads --include-disabled` shows the twelve entries.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks tools`, `uv run pyright`, `uv run pytest` clean; `tests/test_docs.py` confirms the three docs pages list the spec's fields.
- Measured on an Apple M5 Pro, one short sentence through `TTSEngine`: kokoro 631 % → 197 % of a core at `threads=2` (524 → 1046 ms); piper 710 % → 197 % (46 → 93 ms); phonon 542 % → 238 % at 2 and 282 % at 4 (99 → 73 ms) in a fresh process, and no change when a model was loaded first without the field, which the module now warns about.
