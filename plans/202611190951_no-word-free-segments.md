# No word-free segments from `segment_sentences`

**Status:** Done

Implements "No word-free segment" in `specs/tts-module-interface.md` ("`segment_sentences` — sentence grouping for non-incremental backends"): the helper drops a segment with no letter or digit instead of yielding it. It leaves out any text cleaning: text inside a segment stays verbatim, and a word-free complete text given to a backend that does not segment it is untouched.

The cause, measured with a capture sink (duration and level, not by listening): an LLM writing `Sorry… …` got the second `…` cut into a request of its own. A lone `…` came back as 1.1 to 1.2 s at about -40 dBFS on phonon (voice Micah), 0.9 s at -19 dBFS on pocket, 0.55 s at -28 dBFS on kokoro and nothing on piper; a lone emoji made phonon raise `nothing to synthesize`, failing the utterance. Sent whole, `Sorry… …` rendered byte-identical to `Sorry…` on phonon, so nothing is lost by dropping.

## Scope

- `specs/tts-module-interface.md` — the rule and its reason
- `specs/modules/kokoro.md` — the "short silence" sentence, contradicted by the measurement and now moot
- `src/tts_engine/modules/base.py` — `segment_sentences` filters its two yields through `_has_words`
- `tests/modules/test_tts_module_interface.py` — exact outputs, each input whole and in 1- and 4-character pieces

## Steps

1. **Spec.** The "Blank segments are never yielded" sentence becomes the "No word-free segment" bullet; the kokoro spec's error bullet drops its measured-silence claim.
2. **Code.** `_has_words(segment)` is `any(c.isalnum() for c in segment)`; it replaces the blank check on the mid-stream yield and on the tail. The cuts do not move, so prompt yielding, the `max_chars` bound and chunking independence hold as before.
3. **Tests.** Doubled ellipses and dots, a leading ellipsis, a long run, a text with no words, a trailing emoji, a `---` line, a word-free run cut by `max_chars`; unchanged: a single ellipsis, digits, non-ASCII letters.

## Verification

- `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e benchmarks`, `uv run pyright`, `uv run pytest` (1057 passed, 4.4 s).
- The new tests fail with the filter reverted (28 failures).
- Phonon (voice Micah), text stream into a capture sink: `Sorry… …` now gives the audio of `Sorry…` alone, and `Done! 🎉` no longer raises.
