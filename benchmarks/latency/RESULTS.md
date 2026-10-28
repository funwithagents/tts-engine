# Latency benchmark — findings

The human reading of the results under [`results/`](results/). Each result's `summary.md` holds the numbers (one row per entry, every run merged, the latest completed measurement of each entry winning, each row naming its run; `results.csv` has every statistic per mode × text) and its `description.md` says on what device and what it covers; this file says what stood out and which decisions it fed. Updated in the same change as a run that changes a conclusion. Method and options: [README.md](README.md); design: [specs/benchmark.md](../../specs/benchmark.md).

## Result `20261027-1453_mac-m5-pro`

Apple M5 Pro, macOS 26, Python 3.13, the development machine on its home connection. 1 warm-up + 20 measured runs per cell (`say`/`stream` × short/medium), stream token delay 0 ms. Summary: [`results/20261027-1453_mac-m5-pro/summary.md`](results/20261027-1453_mac-m5-pro/summary.md); the runs themselves stay local under `runs/`.

### Runs merged

| Run | When | Scope | Outcome |
|---|---|---|---|
| `20261027-145315` | 2026-10-27 14:53–17:05 | everything except `gemini/*` and `openai-compatible/*` | 41 entries kept. ElevenLabs credits ran out 4 min in (only `flash_v2_5` and the `say` cells of `turbo_v2_5` survived); `mistral/voxtral-mini-tts-2603` rejected by the API; `pocket/english_24l` was a wrong model name; phonon 40m skipped (no `HF_TOKEN`). The two `openai/*` entries were measured here on the socket-per-utterance module and later removed, superseded by `20261028-094201`. |
| `20261027-171640` | 2026-10-27 17:16–18:04 | all 7 ElevenLabs models (after a top-up), `pocket/english_2026-09_24l` | 8 entries clean. phonon 40m failed: gated repos not yet accepted. |
| `20261027-180851` | 2026-10-27 18:09–18:12 | `phonon/40m-en@auto`, `phonon/40m-multilingual@auto` | 2 entries clean. |
| `20261028-094201` | 2026-10-28 09:42–09:50 | `openai/gpt-realtime-2.1-mini`, `openai/gpt-realtime-2.1` | 2 entries clean, on the held-socket module (git `440c60e`). |
| `20261028-095540` | 2026-10-28 09:55–10:10 | `openai-compatible/gpt-4o-mini-tts` (alias), `tts-1`, `tts-1-hd` | 3 entries clean. |
| `20261028-101003` | 2026-10-28 10:10–10:17 | `openai-compatible/gpt-4o-mini-tts-2025-03-20`, `-2025-12-15` (`--include-disabled`) | 2 entries clean. |

50 entries measured (plus `mistral/voxtral-mini-tts-2603`, which only ever errored). Not yet measured: the Gemini entries (rate-limited to 10 req/min per model; `pause_s: 20`; about 50 minutes, mostly pauses).

An earlier run with a 30 ms/word fake-LLM pacing was discarded: it put a ≈340 ms floor under every stream-mode TTFA that had nothing to do with the module. The token delay is 0 by default since.

### Headline (TTFA p50, ms, `say()` on the short text; the full table is in the result's `summary.md`)

| tier | entries | TTFA p50 |
|---|---|---:|
| local, tiny | phonon 31m / 40m-en / 40m-multilingual; pocket 2m / 7m / 18m | 13–35 |
| local, small | pocket 100M (+quantize); piper lessac-low / lessac-medium / ljspeech-medium; pocket 24-layer | 38–86 |
| API, fast | ElevenLabs v3_conversational / flash_v2_5 / v4_turbo; gradium; openai-compatible gpt-4o-mini-tts-2025-03-20 | 112–367 |
| local, LLM-based | qwen3 0.6B–1.7B (Q4_K_M / Q8_0 / BF16) | 168–282 |
| API, mid | ElevenLabs turbo_v2_5; cartesia sonic-3.5 / 3.6 / preview; OpenAI Realtime 2.1-mini; openai-compatible gpt-4o-mini-tts alias / 2025-12-15 | 186–516 |
| slow | piper high voices; mistral voxtral 3.0; deepgram aura / aura-2 / flux; kokoro fp32; ElevenLabs v3 / v4 / multilingual_v2; OpenAI Realtime 2.1; openai-compatible tts-1 | 331–1121 |
| slowest | openai-compatible tts-1-hd | 2034 |

### What stood out

- Local tiny models (phonon, pocket 2m–18m) answer in 13–40 ms, 5–10× faster than the fastest API; stream TTFA equals say TTFA for them, so the measurement isolates the module.
- Fastest APIs: ElevenLabs flash_v2_5, v3_conversational and v4_turbo at 120–180 ms, gradium at 160, the openai-compatible 2025-03-20 snapshot at 350.
- Non-streaming backends show a TTFA that grows with the text and 0.5–1.2 s pauses between sentences: ElevenLabs multilingual_v2 over HTTP, kokoro, piper `high`, openai-compatible `tts-1`/`tts-1-hd`.
- Deepgram Flux is slower than Aura-2; ElevenLabs v3/v4 are 4–5× slower than their turbo/conversational siblings and stall; qwen3 0.6B emits ≈150 ms of silence before voice.
- Reproducibility: ElevenLabs flash_v2_5 matched within 15 ms across two runs; the full OpenAI Realtime 2.1 model swings ±0.3 s between runs.

### What this result changed

- OpenAI Realtime holds its socket across utterances (`440c60e`; [openai spec](../../specs/modules/openai.md)).
- openai-compatible defaults to the `gpt-4o-mini-tts-2025-03-20` snapshot, and the live row follows it (`c362ca3`, `f69efdf`; [openai-compatible spec](../../specs/modules/openai-compatible.md)).
- Deepgram keeps `aura-2-thalia-en` as its default over Flux (`c362ca3`).

## Still to do

1. **Gemini** on the Mac result: `bench.py run --into benchmarks/latency/results/20261027-1453_mac-m5-pro --runs 20 --only "gemini/*"`.
2. **A Raspberry Pi result** for the Reachy Mini target (CM4-class CPU): phonon 31m/40m quantized, piper low/medium, pocket 2m/7m/18m with and without `quantize`, kokoro int8, plus a small API subset for the Wi-Fi path; the planning notes are local for now.
3. Cross-bench comparison and charts (spec open questions).
