# mac-m5-pro

**Device:** Apple M5 Pro (macOS 26.5, arm64), Python 3.13.14. The development machine, otherwise idle during the runs; local models on Metal where the module supports it (phonon `auto`, qwen3), CPU elsewhere.

**Network:** home fibre connection, Paris; provider regions as routed from there. API numbers include that path.

**What:** every module and model the matrix enables except the Gemini entries (rate-limited; still to run), at 1 warm-up + 20 measured runs per cell, stream token delay 0 ms. Six runs on 2026-10-27/28: the bulk pass, an ElevenLabs re-run after a credit top-up, the gated phonon 40m models, the OpenAI Realtime entries re-measured after the module started holding its socket (git `440c60e`), and the openai-compatible models including all three `gpt-4o-mini-tts` ids.

**Notes:** the first run exhausted the ElevenLabs credits 4 minutes in, so every ElevenLabs entry comes from the 17:16 re-run. The OpenAI Realtime rows are from the held-socket module only; the socket-per-utterance numbers (1.24 s mean for 2.1-mini) are quoted in `RESULTS.md`, not kept here. `mistral/voxtral-mini-tts-2603` only ever errored (the API rejects `voice_id` for it). Provider latency depends on the hour: ElevenLabs `flash_v2_5` reproduced within 15 ms across two runs, the full OpenAI Realtime 2.1 model swings ±0.3 s.
