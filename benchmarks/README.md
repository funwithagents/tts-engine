# Latency benchmark — every module and model, through the public API

How long does a caller wait between asking to speak and the first sound, per backend and model, and does the audio then keep up? `bench.py` drives every entry of `matrix.json` through `TTSEngine` with an injected timing sink, records every chunk's arrival, and derives time to first audio, time to first voice, stalls and real-time factor afterwards. Design: [specs/benchmark.md](../specs/benchmark.md). Findings so far: [RESULTS.md](RESULTS.md). Numbers: `results/<result>/summary.md`.

## What is measured, and why this way

The engine's whole point is low-latency streaming: audio reaches the sink chunk by chunk as the backend produces it. The number that matters for an agent is the wait before the first sound, so the benchmark measures it at the engine's public API (the `TTSEngine` an embedder or the MCP `say` tool uses), not inside each SDK: sentence grouping, socket setup and the module's buffering policy are all part of what a user pays.

| Metric | Definition |
|---|---|
| **TTFA** (headline) | `say()` / `say_stream()` called → first non-empty PCM chunk handed to the sink |
| TTFV | call → first chunk with a sample above 500/32767 (≈ −36 dBFS): skips leading-silence chunks, so it is closer to "first audible sound" |
| after 1st sentence | text stream only: first audio relative to the moment the first sentence's last word was sent. Equal to TTFA at the default token delay of 0; with a positive delay it removes the source's pacing from the number |
| first call | TTFA of the very first synthesis after construction (cold TLS/WebSocket, model kernels, lazy voice loading). Excluded from the statistics |
| load s | `TTSEngine(...)` construction: client setup or socket handshake for an API, weight load for a local model (**includes the download on a first-ever run**) |
| max gap / median gap | longest and typical wait between two consecutive chunks: a stall mid-utterance that TTFA alone would hide |
| speed × | audio seconds produced per wall second after the first chunk (the real-time factor; below 1 playback starves) |

**Everything is recorded raw, and the metrics are derived afterwards.** A run's record is the complete timeline: every chunk as `[arrival ms since the call, bytes, peak amplitude]`, plus when the first sentence and the last word were sent (stream mode) and when the call returned. `derive_metrics()` turns that into the metrics above at summary time, and `results.csv` carries the full distribution (n, mean, median, stdev, min, p90, max) of each for every entry × mode × text; `summary.csv` / `summary.md` keep one row per entry with the numbers a reader decides on (TTFA p50 and p90, the stream and long-text TTFA, max gap, speed, load, first call), a Local table then a Hosted one. Every record and every CSV row names the module's `kind` (`hosted`, `local` or `fixture`), read from the library's registry. A new metric is one function line, and `summarize` recomputes it for past results without re-running anything.

Design choices:

- **Injected timing sink, no playback.** No audio hardware is touched, so the benchmark does not compete for the sound device, and playback (which blocks `feed` at 1×) does not distort the measurements. TTFA is identical with or without a real player: the first `feed` happens at the same moment.
- **One subprocess per entry.** Each config gets a cold process: construction cost is measured cleanly, the memory of one local model (torch, 1.7B GGUF) is freed before the next loads, and a hang or crash costs only that entry (per-entry timeout, log file).
- **Both input shapes.** `say` = complete text (MCP tool / library). `stream` = the same text as a word-by-word async source, delivered **as fast as the engine takes it** (`--token-delay-ms 0`, the default), so `stream` TTFA is the module's own streaming-path latency. A positive `--token-delay-ms` (e.g. 30) simulates an LLM instead; TTFA then includes the wait for the first sentence to be emitted (≈ 340 ms for the short text at 30 ms/word, for *every* sentence-grouped backend). The backends differ a lot on this path:
  - native text stream (forward text as it arrives): **elevenlabs, deepgram, cartesia, gradium**
  - sentence-grouped (wait for a full sentence, then synthesize it): **mistral, gemini, openai (Realtime), openai-compatible, pocket, phonon, qwen3, kokoro, piper**
- **Two texts.** `short` is one sentence (57 chars), `medium` is three sentences (237 chars). Several modules synthesize per sentence, so the length of the first sentence matters more than the total.
- **Warm-up, then repeats.** By default each mode × text gets 1 discarded warm-up + 5 measured runs (24 syntheses per entry). A result meant to be kept uses `--runs 20`: local models are near-deterministic, API latency is not, and p90 only means something from twenty samples up.
- **Constant voice per provider.** Only the model varies within a provider. Voices come from `tests-e2e/support.py` / `examples/`.

## Configurations (`matrix.json`)

○ = opt-in (`--include-disabled`). `default` tag = the module's zero-config/default model; `prev` tag = one version back of a model in the list, kept because a version bump can move latency either way (`--exclude-tags prev` for a quicker pass). A version two or more back, or a snapshot its alias already resolves to, is ○.

| Module | Entries (model / variant) | Gate | Why these |
|---|---|---|---|
| tone, audiofile | fixture | none | harness overhead floor (≈ 0 ms) |
| elevenlabs | `eleven_flash_v2_5` (default), ○ `eleven_flash_v2`, `eleven_turbo_v2_5` (prev, deprecated), ○ `eleven_turbo_v2` (deprecated), `eleven_multilingual_v2`, `eleven_v3` (prev), `eleven_v3_conversational` (prev), `eleven_v4`, `eleven_v4_turbo` | `ELEVENLABS_API_KEY` | every current TTS model. `say` goes over HTTP for all; the text stream uses the TTS socket for the v2 family and the text-to-dialogue socket for v3/v4, so the two socket families can be compared head to head. Turbo is deprecated in favour of Flash but still served |
| gradium | `default`, `gradium-tts-beta` | `GRADIUM_API_KEY` | the only two aliases the API accepts; the docs say both route to the same model today, so this doubles as a noise check |
| mistral | `voxtral-mini-tts-3-0` (default), ○ `voxtral-mini-tts-2603` (prev) | `MISTRAL_API_KEY` | Voxtral TTS 3 (Oct 20 2026, preview); 26.03 is listed but disabled: the API rejects `voice_id` for it (400 "voice is not supported for speech v1") and the module requires one |
| deepgram | `aura-2-thalia-en` (default), `aura-asteria-en` (prev), `flux-haley-en` | `DEEPGRAM_API_KEY` | the three generations: Aura-2 and first-gen Aura on speak v1, Flux on speak v2. On Deepgram the model is the voice, so one voice per generation |
| gemini | `gemini-3.8-flash-tts` (default), `gemini-3.8-flash-lite-tts`, `gemini-3.1-flash-tts-preview` (prev), `gemini-2.5-pro-preview-tts` (prev), ○ `gemini-2.5-flash-preview-tts` | `GEMINI_API_KEY` | the four ids in the docs' table, plus the 2.5 Flash preview that the SDK still names; `pause_s: 20` for the 10 req/min/model limit, and **100 requests per model per day** on this key's tier: an entry costs (warmup + runs) × 8 requests, so `--runs 10` is the most one day allows per model (the medium text is 3 requests per run) |
| cartesia | `sonic-3.6` (default), `sonic-3.5` (prev), ○ `sonic-3`, `sonic-preview` | `CARTESIA_API_KEY` | every alias still served (`sonic-latest` is deprecated; `sonic-2`/`sonic-turbo` sunset on Oct 20 2026). Each alias tracks one stable snapshot, so the dated ids add nothing |
| openai (Realtime) | `gpt-realtime-2.1-mini` (default, voice `marin`), `gpt-realtime-2.1`, ○ `gpt-realtime-2` | `OPENAI_API_KEY` | OpenAI's successor path; one Realtime socket held for the module's lifetime since `440c60e`, so the handshake lands in `load s`, not in TTFA; sentence-grouped stream. Per-token billing |
| openai-compatible | `gpt-4o-mini-tts-2025-03-20` (default since `c362ca3`, voice `marin`), `gpt-4o-mini-tts` (the alias, erratic), ○ `gpt-4o-mini-tts-2025-12-15` (the alias resolves to it), `tts-1`, `tts-1-hd` (voice `alloy`) | `OPENAI_API_KEY` | the `/v1/audio/speech` module; all the ids the SDK types. **All are deprecated** (announced 2026-10-01, shutdown Jan 6 2027). `gpt-4o-mini-tts` is erratic (minutes per request on bad days); each run is cut at `--run-timeout-s` |
| pocket | `english_2026-10` (default, pinned), `english_2026-10` + `quantize`, `english_2026-09_24l`, `english_18m_2026-10`, `english_7m_2026-10`, `english_2m_2026-10`, each tiny model also + `quantize`, ○ `english_2026-10@mps` | `pocket` extra | the English size ladder (100M, 24-layer, 18M, 7M, 2M), each but the 24-layer also as int8 (the point on an ARM CPU); the `_refl` siblings are the same size and the older dated models the same architecture, so neither is listed |
| phonon | `31m-en@auto` (default, Metal), `31m-en@cpu`, `31m-en@cpu+q8_0`, `31m-en@cpu+q4k`, `40m-en` and `40m-multilingual`, each `@auto`, `@cpu`, `@cpu+q8_0`, `@cpu+q4k` | `phonon` extra; 40m needs `HF_TOKEN` and the gated repos accepted | device and quantization effect, on both gated 40M checkpoints relevant to English too (a CPU-only host runs them quantized) |
| qwen3 | 0.6B CustomVoice `Q8_0` (default), `Q4_K_M`, `BF16`; 1.7B CustomVoice `Q8_0`, `Q4_K_M`, `BF16`; 1.7B VoiceDesign `Q8_0` | `qwen3` extra (Apple silicon) | the full size × quant grid of the models the module accepts |
| kokoro | `v1.0-fp32` (default), ○ `v1.0-int8`, ○ `v1.0-fp16` | `kokoro` extra | int8/fp16 exist only as GitHub release assets (`model-files-v1.1`): put them in `models/` (git-ignored) first, then `--include-disabled` |
| piper | `en_US-ljspeech-medium` (default), `en_US-ljspeech-high`, `en_US-lessac-{low,medium,high}` | `piper` extra | same speaker across quality tiers isolates the model-size effect |

To add or change a config, edit `matrix.json`. Each entry is `{id, type, config}`, where `config` is the `engine.module` block without `type`. Optional keys: `modes`, `pause_s`, `timeout_s`, `requires_env`, `enabled`, `tags`, `notes`. Relative paths resolve against the repo root. A new backend in the library gets its rows here ([AGENTS.md](../AGENTS.md), "Adding a new TTS module").

## Runs and results

Two trees under this folder:

- **`runs/<timestamp>/`** — every run as it happened: `env.json` (platform, git SHA, library versions, settings, selection), the plain `raw.jsonl` the workers append to, `logs/<entry>.log`. Working data, **git-ignored**. Run as many as you like.
- **`results/<YYYYMMDD-HHMM>_<name>/`** — a benchmark worth keeping, merged from any number of runs on one host: `description.md` (yours: device, network, what it covers, what affects comparability), `runs.json` (every run merged so far, its environment, the entries it currently supplies), `raw.jsonl.gz` (the merged records, each tagged with its run) and the generated `results.csv` (everything, per cell), `summary.csv` and `summary.md` (one row per entry, local backends then hosted). **Committed.**

Merging rule: for each entry, the latest run that **completed** it (a `done` record) supplies every record, so a newer run overrides an older measurement and an interrupted re-run never shadows a complete one; an entry no run completed keeps its latest attempt, so the summary can say why it is missing. Superseded measurements leave the result (they stay in `runs/` locally and in git history). Re-merging a run replaces what it had contributed.

## Running it

From the repo root. The API keys live in `~/.zshrc`, so source it (as for `tests-e2e`).

```bash
# Dry check: which entries would run or skip, and why. Synthesizes nothing.
zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run python benchmarks/bench.py list'

# Smoke test of the harness: the tone fixture, no network, no audio device, ~10 s
uv run python benchmarks/bench.py run --only tone --runs 2

# A result to keep: create it from its first run, then merge runs into it as they come.
# 1. the first run, unattended (kept awake, detached, logged), 20 runs per cell
nohup caffeinate -i zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run python benchmarks/bench.py run --runs 20 --exclude "gemini/*"' > benchmarks/pass.log 2>&1 &
# 2. the result folder, dated from that run, with a description to fill in
uv run python benchmarks/bench.py new mac-m5-pro --from-run benchmarks/runs/<timestamp>
uv run python benchmarks/bench.py merge benchmarks/results/<result> benchmarks/runs/<timestamp>
# 3. later runs merge on completion (latest completed measurement of an entry wins)
zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run python benchmarks/bench.py run --into benchmarks/results/<result> --runs 20 --only "gemini/*"'
... bench.py run --into benchmarks/results/<result> --runs 20 --only "openai/*"   # after a module change

# Quick pass: every module's default model only
... bench.py run --tags default

# Subsets
... bench.py run --only 'cartesia/*' 'deepgram/*'
... bench.py run --tags local --exclude-tags heavy
... bench.py run --tags api --runs 10 --modes stream --texts medium

# Rebuild a result's summary (or a single run's, for a look before merging)
uv run python benchmarks/bench.py summarize benchmarks/results/<result>
uv run python benchmarks/bench.py summarize benchmarks/runs/<timestamp>
```

**Stopping and resuming.** Every run's record is appended to the run's `raw.jsonl` the moment it completes, and an entry gets a `done` record when its worker finishes, so nothing measured is ever lost:

- clean stop: `touch benchmarks/runs/<timestamp>/STOP` — the running entry finishes, the summary is written (and the run merged if `--into` was given), the script exits;
- immediate stop: Ctrl-C, or `kill <pid>` of the `bench.py` parent (`pkill -f "bench.py run"` kills parent and worker together) — the current entry is dropped and recorded as `interrupted`;
- resume: `bench.py run --resume benchmarks/runs/<timestamp>` — same settings and selection (read back from `env.json`), entries with a `done` record are kept, the others run again from scratch (a half-measured entry's partial records are discarded first, so it is never counted twice). Merge it again afterwards: a re-merged run replaces its earlier contribution.

`run` options: `--only` / `--exclude` (id globs), `--tags` / `--exclude-tags`, `--runs` (5), `--warmup` (1), `--modes say stream`, `--texts short medium`, `--token-delay-ms` (0; e.g. 30 for LLM pacing), `--pause-s` (0.5), `--run-timeout-s` (120), `--entry-timeout-s` (600: the load/download allowance; each run adds `run_timeout_s` + pause to the entry's budget, so it scales with `--runs`), `--include-disabled`, `--into`, `--resume`, `--runs-dir`.

## Before running: caveats

- **Do not run it alongside other live tests, or another benchmark.** API rows share rate limits with `tests-e2e` (Gemini especially), and local rows saturate CPU/GPU, so concurrent load inflates the numbers in both directions.
- **First-ever run downloads models.** Download time lands in `load s` and can exceed `--entry-timeout-s` for qwen3 1.7B BF16 or pocket. To fill the Hugging Face cache, do one throwaway `--tags local --runs 1 --warmup 0` pass first (or raise the timeout). Downloads total several GB, qwen3 alone ≈ 8 GB across its six entries.
- **API cost and credits.** Each entry makes 24 syntheses at n=5 (84 at n=20) of 57 + 237 characters: 3,528 / 12,348 characters per entry. The enabled API entries come to ≈ 95 k characters at n=5 and ≈ 330 k at `--runs 20`. ElevenLabs' seven enabled entries are 24,696 / 86,436 characters: on USD billing ($0.04 per 1k for Flash, Turbo, v3 Conversational and v4 Turbo; $0.08 per 1k for Multilingual v2, v3 and v4) ≈ $1.41 / ≈ $4.94; on a subscription's included characters, where the $0.04 models count half, ≈ 17.6 k / ≈ 62 k credits. **Check the account's remaining credits before an unattended run**: the first Mac run exhausted them 4 minutes in. OpenAI Realtime is billed per token (≈ $20 per 1M audio tokens), a few cents per entry. The other providers are not priced here.
- **Duration.** Each run lasts TTFA plus the backend's generation of the whole utterance (≈ 4 s and ≈ 15 s of speech), plus the pause. At `--runs 20`: API entries 2–5 min each, local entries 1–5 min plus model load, Gemini ≈ 35 min per entry at its 20 s pause (`--texts short` halves it); the Mac result's first run took 2h12 for 43 entries.
- **Network-bound numbers** reflect your connection and the provider region at that moment. Run API rows a couple of times at different hours before drawing conclusions; a result keeps the latest, `runs/` keeps them all.
- **Only merge runs made on merged code.** A run made on a branch measures that branch; `env.json` records the git SHA, and the result's "Runs" table shows it.

## Model catalog (verified 2026-10-27/28)

Sources: the installed SDKs' typed literals (`openai.types.audio.SpeechModel`, Cartesia/Deepgram/ElevenLabs/google-genai string literals, `pocket_tts` model names, `ptts.available_quants()`, `qwentts_cpp` known ids), the providers' docs pages, the Hub catalogs, and the OpenAI `/v1/models` listing for the account.

**Reachable through the modules, and in the matrix**

| Provider | Current | Older / deprecated, still served |
|---|---|---|
| ElevenLabs | `eleven_flash_v2_5`, `eleven_flash_v2`, `eleven_multilingual_v2` (TTS socket); `eleven_v3`, `eleven_v3_conversational`, `eleven_v4`, `eleven_v4_turbo` (dialogue socket) | `eleven_turbo_v2_5`, `eleven_turbo_v2` (deprecated → Flash) |
| Gradium | `default` | `gradium-tts-beta` (alias; same model today) |
| Mistral | `voxtral-mini-tts-3-0` (Oct 20 2026, preview) | `voxtral-mini-tts-2603` (Mar 23 2026; rejects `voice_id`) |
| Deepgram | `flux-<voice>-<lang>` (speak v2, ≈ 30 English voices + `flux-general-en`/`-multi`); `aura-2-<voice>-<lang>` (speak v1, 7 languages, ≈ 100 voices) | `aura-<voice>-en` (first gen, 12 voices) |
| Gemini | `gemini-3.8-flash-tts`, `gemini-3.8-flash-lite-tts` | `gemini-3.1-flash-tts-preview`, `gemini-2.5-pro-preview-tts`; `gemini-2.5-flash-preview-tts` only in the SDK |
| Cartesia | `sonic-3.6` (= `sonic-3.6-2026-08-27`), `sonic-preview` | `sonic-3.5` (= `-2026-05-04`), `sonic-3` (= `-2026-01-12`) |
| OpenAI | Realtime (`openai` module): `gpt-realtime-2.1-mini`, `gpt-realtime-2.1`, `gpt-realtime-2` | `/v1/audio/speech` (`openai-compatible` module): `gpt-4o-mini-tts` (snapshots `-2025-03-20`, `-2025-12-15`), `tts-1`, `tts-1-hd` (and the `-1106` snapshots), all shut down Jan 6 2027; `gpt-realtime-mini` (pre-2.1) shuts down Jan 20 2027 |
| pocket-tts 4.0.1 | `english_2026-10` (= `english`), `english_2026-09_24l` / `english_2026-04_24l`, `english_{18m,7m,2m}_2026-10` (+ `_refl_` twins), `english_drifting_26-09`; `french/german/italian/portuguese/spanish/dutch` (+ `_24l`) | `english_2026-01`, `-04`, `-09`, `-09_2` |
| Phonon (ptts 0.5) | `gradium/phonon-31m-en` (Apache-2.0) | gated CC BY-NC: `phonon-40m-{en,fr,de,es,pt,multilingual}`. Quants: f32, q8_0, q8_1, q8k, q6k, q5_0, q5_1, q5k, q4_0, q4_1, q4k (CPU only) |
| Qwen3-TTS (qwentts.cpp) | `0.6B-CustomVoice`, `1.7B-CustomVoice`, `1.7B-VoiceDesign`; quants F32, BF16, Q8_0, Q4_K_M | — |
| Kokoro | `kokoro-v1.0.onnx` fp32 (pinned Hub mirror); fp16 and int8 on the GitHub `model-files-v1.1` release; `kokoro-v1.1-zh` (Chinese) | — |
| Piper (`rhasspy/piper-voices`) | en_US: amy (low, medium), arctic, bryce, danny (low), hfc_female, hfc_male, joe, john, kathleen (low), kristin, kusal, l2arctic, lessac (low, medium, high), libritts (high), libritts_r, ljspeech (medium, high), mike, norman, reza_ibrahim, ryan (low, medium, high), sam; en_GB: alan (low, medium), alba, aru, cori (medium, high), jenny_dioco, northern_english_male, semaine, southern_english_female (low), vctk | — |

**Not reachable through the current modules** (would need module changes; listed so they aren't mistaken for omissions)

- Mistral `voxtral-mini-tts-latest`: rejects `voice_id` on speech v1 (spec); `voxtral-mini-tts-realtime-3-0` is the voice-agents stack only.
- Qwen3-TTS `-Base` models (cloning): rejected by the module in v1.
- Phonon `phonon-31m-en-audiocpp` / `-llamacpp`: exports for other runtimes, not `ptts`.
