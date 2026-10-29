# Latency benchmark — findings

The human reading of the results under [`results/`](results/). Each result's `summary.md` holds the numbers (one row per entry, every run merged, the latest completed measurement of each entry winning, each row naming its run; `results.csv` has every statistic per mode × text) and its `description.md` says on what device and what it covers; this file says what stood out and which decisions it fed. Updated in the same change as a run that changes a conclusion, and so is the result's hand-written `description.md` whenever a merge changes what the result covers. Method and options: [README.md](README.md); design: [specs/benchmark.md](../specs/benchmark.md).

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
| `20261028-114337` | 2026-10-28 11:43–12:12 | `deepgram/*`, `cartesia/*` | 6 entries clean, on the held-socket modules (git `418857e`); they replace the socket-per-utterance measurements of the first run. |
| `20261028-122151` | 2026-10-28 12:21–12:33 | the nine CPU-oriented entries added for the Raspberry Pi plan: pocket 2m / 7m / 18m with `quantize`, phonon 40m-en and 40m-multilingual at `cpu`, `cpu+q8_0`, `cpu+q4k` | 9 entries clean: the Mac baseline for the entries a Pi result will run. |
| `20261028-124154` | 2026-10-28 12:41–12:46 | `kokoro/v1.0-int8`, `kokoro/v1.0-fp16` (`--include-disabled`, the GitHub release exports placed in `models/`) | 2 entries clean. |
| `20261028-123808` | 2026-10-28 12:38–13:22 | `gemini/*` at 20 runs per cell | Stopped: the key's tier allows **100 requests per model per day**, and an entry at n=20 costs 168. `3.8-flash-tts` complete but for its stream/medium cell (1 run, then 429s); `3.8-flash-lite-tts` interrupted with three cells done. |
| `20261028-132417` | 2026-10-28 13:24–14:03 | `gemini-3.1-flash-tts-preview`, `gemini-2.5-pro-preview-tts` at 10 runs per cell | 2 entries clean except 2.5-pro's stream/medium cell (5 runs, then the daily 429). |
| `20261029-103910` | 2026-10-29 10:39–11:08 | the eighteen `threads`-tagged entries: phonon 31m `cpu` and `cpu+q8_0`, kokoro fp32 and int8, piper ljspeech-medium and lessac-low, each at `+t1`, `+t2`, `+t4` (`--include-disabled` for the int8 rows) | 17 entries clean, the first with CPU and memory columns (git `cfd9e20`). `kokoro/v1.0-int8+t1` never produced audio (an empty output from the int8 export at one intra-op thread, reproduced outside the benchmark; fp32 and fp16 are fine at one thread, int8 at two) and is listed as not measured. |
| `20261029-111334` | 2026-10-29 11:13–12:30 | every other local entry (`--tags local --exclude-tags threads --include-disabled`, minus `pocket/*@mps`): the 36 uncapped rows, kokoro int8/fp16 included | 36 entries clean, re-measured for the CPU and memory columns (git `bb505e7`, the result branch, whose library code is the merged `cfd9e20`); they replace the 27/28 October measurements of the same entries. |

88 entries measured (plus `mistral/voxtral-mini-tts-2603`, which only ever errored). Gemini is measured at 10 runs per cell at best: this key allows 100 requests per model per day, and `3.8-flash-tts` / `3.8-flash-lite-tts` still need a fresh day at `--runs 10` (their rows come from the stopped n=20 run and lack a cell each).

An earlier run with a 30 ms/word fake-LLM pacing was discarded: it put a ≈340 ms floor under every stream-mode TTFA that had nothing to do with the module. The token delay is 0 by default since.

### Headline (TTFA p50, ms, `say()` on the short text; the full table is in the result's `summary.md`)

| tier | entries | TTFA p50 |
|---|---|---:|
| local, tiny | phonon 31m / 40m (Metal and CPU variants); pocket 2m / 7m / 18m (fp32 and int8) | 9–35 |
| local, small | pocket 100M (+quantize); piper lessac-low / lessac-medium / ljspeech-medium; pocket 24-layer | 37–86 |
| API, fast | cartesia sonic-3.5 / 3.6 / preview (held socket); ElevenLabs v3_conversational / flash_v2_5 / v4_turbo; gradium; deepgram flux / aura / aura-2 (held socket); openai-compatible gpt-4o-mini-tts-2025-03-20 | 83–367 |
| local, mid | qwen3 0.6B–1.7B (Q4_K_M / Q8_0 / BF16); kokoro int8 | 168–282 |
| API, mid | ElevenLabs turbo_v2_5; OpenAI Realtime 2.1-mini (held socket); openai-compatible gpt-4o-mini-tts alias / 2025-12-15; gemini 3.8-flash-lite | 186–521 |
| slow | piper high voices; mistral voxtral 3.0; kokoro fp32 / fp16; ElevenLabs v3 / v4 / multilingual_v2; OpenAI Realtime 2.1; openai-compatible tts-1; gemini 3.8-flash / 3.1-flash-preview | 331–1121 |
| slowest | openai-compatible tts-1-hd; gemini 2.5-pro-preview | 2034–5081 |

### What stood out

- Local tiny models (phonon, pocket 2m–18m) answer in 13–40 ms, 5–10× faster than the fastest API; stream TTFA equals say TTFA for them, so the measurement isolates the module.
- Fastest APIs: Cartesia at 83–86 ms once its socket is held (it was 203–206 per utterance), then ElevenLabs flash_v2_5, v3_conversational and v4_turbo at 112–136, gradium at 155–165, Deepgram at 174–213 (was 394–516), the openai-compatible 2025-03-20 snapshot at 367.
- Non-streaming backends show a TTFA that grows with the text and 0.5–1.2 s pauses between sentences: ElevenLabs multilingual_v2 over HTTP, kokoro, piper `high`, openai-compatible `tts-1`/`tts-1-hd`.
- With the socket held, Deepgram Flux reaches first audio before Aura-2 (174 vs 213 ms) but generates slower (1.5× vs 2.2× real time), the reverse of the per-utterance picture that fixed Aura-2 as the default. ElevenLabs v3/v4 are 4–5× slower than their turbo/conversational siblings and stall; qwen3 0.6B emits ≈150 ms of silence before voice.
- Reproducibility: ElevenLabs flash_v2_5 matched within 15 ms across two runs; the full OpenAI Realtime 2.1 model swings ±0.3 s between runs.
- On the M5, int8 does not pay: pocket `quantize` is slower than fp32 at every size (2m 16 → 21 ms, 7m 22 → 30, 18m 26 → 35, 100M 38 → 77) with a lower real-time factor; phonon 40m on CPU is 21–26 ms against 14 on Metal, `q8_0` the best CPU variant (21 ms, 44× real time) and `q4k` the slowest and noisiest (p90 up to 71 ms). Whether int8 helps on an ARM CPU without Metal is exactly what the Pi result has to show.
- Gemini: the flash models reach first audio in 0.5–0.9 s (lite 521 ms, 3.8-flash 691, 3.1-preview 862), the 2.5-pro preview in 5 s on the short text and 18 s on the three-sentence one, generating barely faster than real time. Daily quota, not latency, is what limits measuring them.
- The exception is kokoro, which runs on onnxruntime: int8 halves its TTFA (253 vs 527 ms) and more than doubles its speed (20.6× vs 8.9× real time), while fp16 changes nothing (512 ms). int8 is the kokoro to try on the Pi.

- **CPU and memory** (every local entry, 2026-10-29; CPU in percent of one core, every thread summed). Uncapped, the onnxruntime and ptts CPU paths take every core they find: piper 720–830 %, kokoro 750–815 %, phonon on CPU 600–700 % and its `q4k` variants 930–1020 % (ten cores for a 40M model). phonon on Metal needs 38–49 % and qwen3 on Metal 23–38 %, the GPU doing the work; pocket stays at 143–194 % because its library pins torch to one thread. Peak memory: phonon 250–550 MB, piper 335–565 MB, kokoro 540–870 MB, pocket 440–800 MB for the tiny models, 1.0–1.15 GB for 100M and 2.9 GB for the 24-layer model, qwen3 2.9–5.7 GB. On a Pi with about 2 GB free beside its daemon, that rules out the pocket 24-layer model and every qwen3 before any speed question.
- **Thread caps.** phonon's default pool oversubscribes a 31M model: every cap beats the uncapped row on time to first audio and real-time factor, same day (`31m-en@cpu`: 18 ms / 40× / 603 % uncapped against 12 ms / 48× / 191 % at one thread, 14 / 50× / 249 % at two, 12 / 52× / 348 % at four; `+q8_0`: 14 ms / 50× / 662 % against 10 / 56× / 177 %, 12 / 64× / 239 %, 9 / 61× / 328 %), and one thread still reads 177–191 % of a core, so ptts works outside its tensor pool too. kokoro and piper scale with the threads they get and read exactly the thread count (107–118 %, 210–223 %, 410–434 %): kokoro fp32 1847 → 1033 → 620 ms at one, two, four threads (528 uncapped), 2.6× → 4.8× → 7.8× real time; int8 434 → 301 ms (262 uncapped) at 12.4× → 17.8×, and no audio at all at one thread; piper ljspeech-medium 184 → 108 → 68 ms (66 uncapped) at 28× → 50× → 86×, lessac-low 115 → 78 → 46 ms (49 uncapped) at 44× → 78× → 127×.
- **What this says for the Pi** (four A72 cores, about 1.5 of them taken by the daemon): phonon `q8_0` at one thread (10 ms, 56× real time on one M5 core, 177 % of a core, 387 MB) and the tiny pocket models (15–17 ms at 160–180 %, 440–570 MB) are the candidates that leave the daemon its cores with margin, even twenty times slower per core; piper lessac-low at one or two threads (115 / 78 ms, 44× / 78×) is the fallback; kokoro int8 needs two threads (434 ms, 12×) and cannot run on one; kokoro fp32 is out (2.6× on one core, 1.8 s to first audio).
- Reproducibility across days, local entries: 19 of the 36 re-measured rows moved by 2 ms or less of TTFA p50 and 27 by 5 ms or less; the largest moves were qwen3 0.6B BF16 (231 → 212 ms) and piper lessac-high (331 → 317).

### What this result changed

- OpenAI Realtime holds its socket across utterances (`440c60e`; [openai spec](../specs/modules/openai.md)).
- openai-compatible defaults to the `gpt-4o-mini-tts-2025-03-20` snapshot, and the live row follows it (`c362ca3`, `f69efdf`; [openai-compatible spec](../specs/modules/openai-compatible.md)).
- Deepgram keeps `aura-2-thalia-en` as its default over Flux (`c362ca3`), decided on per-utterance numbers; with the held socket Flux leads on TTFA and trails on generation speed, so the choice is worth a second look.
- The `threads` field on phonon, kokoro and piper (`cfd9e20`; [phonon](../specs/modules/phonon.md), [kokoro](../specs/modules/kokoro.md), [piper](../specs/modules/piper.md) specs) was measured here; a phonon default below its own, since the pool is oversubscribed on a many-core host, is left to the Pi result, where the candidate values run on the actual core count.

## Still to do

1. **Gemini 3.8-flash and 3.8-flash-lite** on a fresh day (the daily quota resets about 19 h after it was hit): `bench.py run --into benchmarks/results/20261027-1453_mac-m5-pro --runs 10 --only "gemini/gemini-3.8-flash-tts" "gemini/gemini-3.8-flash-lite-tts"`; a higher billing tier would allow n=20.
2. **A Raspberry Pi result** for the Reachy Mini target (CM4-class CPU): phonon 31m/40m quantized, piper low/medium, pocket 2m/7m/18m with and without `quantize`, kokoro int8, plus a small API subset for the Wi-Fi path; the planning notes are local for now.
3. **ElevenLabs `flash_v2_5` and `v4_turbo` re-measured** for the CPU of a hosted path (the MP3 decode through miniaudio): about 4 min and a few cents. The other hosted entries keep their 27/28 October measurements and read `—` in the resource columns.
4. Cross-bench comparison and charts (spec open questions); GPU usage is deferred (spec open question 4).
5. **A `threads` default for phonon** (`2`, or `1` on a small board), decided on the Pi result rather than on the Mac's oversubscribed pool.
