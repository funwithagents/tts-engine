# Credits — Reachy Mini voice audition

Every sample on this page is **AI-generated speech**: no recording is a human voice speaking these words. The samples are published as **non-commercial comparison material**, to show how candidate voices sound for one application. They are not for reuse; the terms of each provider or model below apply to its samples.

The page and the tool that rendered it are part of [tts-engine](https://github.com/funwithagents/tts-engine) (the [audition file](https://github.com/funwithagents/tts-engine/blob/main/examples/auditions/reachy-mini.json)). No provider endorses this page, and none was involved in it.

> These terms are a reading of the published licences and terms of service, checked on 2026-10-29 and 2026-10-30. They are not legal advice.

## Local models

The audio comes from an open model run on our machine. The model's licence and, where it differs, the licence of the voice's training data or reference recording both apply.

### Phonon (Gradium)

- `phonon-31m-micah`, `phonon-31m-micah-flat`, `phonon-31m-easton`, `phonon-31m-alfie`, `phonon-31m-teddy`, `phonon-31m-trevor`, `phonon-31m-spencer`, `phonon-31m-hugh`, `phonon-31m-fennick`: [Phonon 31m-en](https://huggingface.co/gradium/phonon-31m-en) by Gradium, **Apache-2.0** (weights, tokenizer and voices); voices from [gradium/phonon-voices](https://huggingface.co/gradium/phonon-voices), Apache-2.0.
- `phonon-40m-micah`: [Phonon 40m-en](https://huggingface.co/gradium/phonon-40m-en) by Gradium, **CC BY-NC 4.0** (non-commercial).

### Pocket TTS (Kyutai)

[Pocket TTS](https://huggingface.co/kyutai/pocket-tts) by Kyutai, **CC BY 4.0**. Each preset voice is conditioned on a reference recording from [kyutai/tts-voices](https://huggingface.co/kyutai/tts-voices):

- `pocket-michael`: voice `michael`, VCTK p360, from the VCTK corpus (CSTR, University of Edinburgh), CC BY 4.0
- `pocket-george`: voice `george`, VCTK p315, from the VCTK corpus (CSTR, University of Edinburgh), CC BY 4.0
- `pocket-charles`: voice `charles`, VCTK p254, from the VCTK corpus (CSTR, University of Edinburgh), CC BY 4.0
- `pocket-paul`: voice `paul`, VCTK p259, from the VCTK corpus (CSTR, University of Edinburgh), CC BY 4.0
- `pocket-marius`: voice `marius`, Kyutai voice donations, CC0
- `pocket-18m-michael`: voice `michael`, VCTK p360, from the VCTK corpus (CSTR, University of Edinburgh), CC BY 4.0

### Piper

Voices from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices); each voice's `MODEL_CARD` names its training data:

- `piper-arctic-bdl`: `en_US-arctic-medium` (speaker `bdl`) — CMU ARCTIC (Carnegie Mellon University), permissive with attribution; fine-tuned from `lessac`
- `piper-arctic-rms`: `en_US-arctic-medium` (speaker `rms`) — CMU ARCTIC (Carnegie Mellon University), permissive with attribution; fine-tuned from `lessac`
- `piper-ryan-medium`: `en_US-ryan-medium` — RyanSpeech data, CC BY-NC-SA 4.0; fine-tuned from `lessac` (research-only data, see below)
- `piper-joe-medium`: `en_US-joe-medium` — CC0 data; fine-tuned from `lessac`
- `piper-ryan-low`: `en_US-ryan-low` — RyanSpeech data, CC BY-NC-SA 4.0
- `piper-sam-medium`: `en_US-sam-medium` — Apache-2.0 data; fine-tuned from `lessac`

Samples of voices trained on RyanSpeech (`ryan-*`) are shared under **CC BY-NC-SA 4.0**. Several voices are fine-tuned from `en_US-lessac`, whose base data (Blizzard 2013) is licensed for **research purposes only**. They are shown here only to compare voices, not for any other use.

### Qwen3-TTS (Alibaba Qwen)

- `qwen3-1.7b-aiden-bf16`, `qwen3-voicedesign-companion`: [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) by Alibaba Qwen (1.7B CustomVoice, 1.7B VoiceDesign), **Apache-2.0**; preset speaker `aiden`, and one designed voice.

### Kokoro

- `kokoro-am-puck`, `kokoro-am-fenrir`, `kokoro-am-michael`, `kokoro-bm-george`: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad, **Apache-2.0**, int8 ONNX export by [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).

## Hosted APIs

The audio was generated through the provider's API, on our account. Under each provider's terms the account holder owns the output or holds the rights to it. None of them asks for attribution. Mistral, Deepgram and OpenAI require that the output not be presented as human speech: on this page, every voice is AI-generated and not a human voice.

- **ElevenLabs** — `elevenlabs-river-v3c`, `elevenlabs-river-flash-flat`, `elevenlabs-liam-v3c`, `elevenlabs-will-v3c`, `elevenlabs-charlie-v4t`. Generated on a paid plan; [terms of use](https://elevenlabs.io/terms-of-use).
- **Cartesia** (Sonic) — `cartesia-zeke`, `cartesia-eden`, `cartesia-jake`, `cartesia-corey`, `cartesia-liam`, `cartesia-henry`, `cartesia-daniel`, `cartesia-zeke-happy`. Generated on a paid plan; [terms](https://cartesia.ai/legal/terms).
- **Gradium** — `gradium-spencer`, `gradium-micah`, `gradium-alfie`. Generated on a paid plan; [terms of service](https://gradium.ai/legal/terms-of-service-gradium-2026-07-22.pdf).
- **Mistral** (Voxtral TTS) — `mistral-james`, `mistral-rob`, `mistral-hugo`, `mistral-howard`, `mistral-fred`, `mistral-david`. [Commercial terms of service](https://legal.mistral.ai/terms/commercial-terms-of-service).
- **Deepgram** (Aura-2) — `deepgram-apollo`, `deepgram-aries`. [Terms](https://deepgram.com/terms).

- **Google** (Gemini API, Gemini 3.8 flash lite TTS) — `gemini-puck`, `gemini-fenrir`, `gemini-achird`. [Gemini API terms](https://ai.google.dev/gemini-api/terms).
- **OpenAI** (Realtime API, `gpt-realtime-2.1-mini`) — `openai-cedar`, `openai-alloy`, `openai-verse`; (speech API, `gpt-4o-mini-tts`) — `openai-compatible-fable`, `openai-compatible-ash-robotic`. [Services agreement](https://openai.com/policies/services-agreement/).

Product names belong to their owners. They are used here only to say where each sample comes from.
