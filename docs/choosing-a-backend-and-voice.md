# Choosing a backend and a voice

Thirteen providers and hundreds of voices are more than anyone wants to try one by one. This page narrows them down in four steps, each answered by a different part of the project: where a backend can run, how fast it answers, which voices it has, and how they sound.

## 1. Where can it run?

Every module declares its `kind` ([modules/index.md](modules/index.md)):

- **`local`** (`pocket`, `phonon`, `qwen3`, `kokoro`, `piper`): an in-process model. No key, no network once the weights are downloaded (on first use), no per-character cost; it needs disk space and CPU or GPU time on the machine that speaks.
- **`hosted`** (`elevenlabs`, `gradium`, `mistral`, `deepgram`, `gemini`, `cartesia`, `openai`, `openai-compatible`): a cloud API. A key, a network path to the provider, and usually a bill or a quota. `openai-compatible` can also target a server you host yourself, through `base_url`.
- **`fixture`** (`tone`, `audiofile`): no synthesis, for tests and demos only.

Playback is always local, whichever backend synthesizes.

Then check the platform and the licenses on the module's page:

- `qwen3` installs on Apple-silicon Macs only (a Linux CUDA host can install its wheel by hand).
- On Intel Macs, `pocket`, `kokoro` and `piper` do not install; `kokoro` does not install on Python 3.14 either.
- `pocket` pulls in PyTorch; `phonon`, `qwen3`, `kokoro` and `piper` do not.
- `piper` and `kokoro`'s pronunciation step are GPL; the multilingual `phonon` models are CC BY-NC; each `piper` voice has its own dataset license.
- A hosted provider's quota can matter more than its latency: Gemini, for one, allows a few requests per model per minute on its lower tiers, and it sends one request per sentence.

## 2. How fast does it answer?

The engine exists for low latency, and backends differ by an order of magnitude. The [latency benchmark](../benchmarks/latency/RESULTS.md) measures every module and model through the same `TTSEngine` API: its headline table groups them into tiers by time to first audio, and the result's `summary.md` has every number, on the machine and connection its `description.md` names. Hosted numbers depend on your network and the hour; local ones on your hardware.

Two things the headline does not show but matter for an agent:

- **How it handles a text stream.** A backend that forwards an LLM's tokens as they arrive starts speaking before the first sentence is complete; one that synthesizes sentence by sentence waits for each sentence to end ([streaming.md](streaming.md)).
- **Pauses between sentences.** A backend that does not stream its own output delivers each sentence in one burst, so its first-audio time grows with the sentence length.

## 3. Which voices does it have?

[`list_voices`](voices.md) reads a backend's own catalog and returns each voice with the config fields that select it, its languages, and, when the catalog says so, its gender, age and description. Gemini, OpenAI Realtime and `openai-compatible` have no listing endpoint; their module pages say where the voices are documented.

The metadata is uneven: phonon, pocket, piper and qwen3 give no gender, so a filter on gender drops all their voices unless you keep the unknown ones.

## 4. How do they sound?

Only listening decides. The [voice audition tool](../tools/voice-audition/README.md) turns the previous steps into a page:

1. `new` builds a candidate file from the catalogs, filtered by gender, language or text, a few voices per backend;
2. `render` speaks one sentence with every candidate through `TTSEngine` and measures its time to first audio on your machine;
3. the page plays them one by one and lets you mark each Yes, Maybe or No, then export the choices.

[`examples/demo.json`](../tools/voice-audition/examples/demo.json) is a hand-picked starting point with two or three conversational voices per backend. The latency the audition shows is your machine's, three runs on one sentence; it is not comparable to the benchmark's controlled numbers, and both are right for what they measure.

A candidate's `config` is a complete `module` block: the voice you keep goes into your engine configuration as is ([configuration.md](configuration.md)).
