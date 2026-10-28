# TTS modules

A module is a swappable synthesis backend. Its `type` selects an entry from the module registry, while every other field in the `module` object is specific to that backend. Changing modules does not change `TTSEngine`, playback, tools, or MCP usage.

| Module | `kind` | Synthesis | Installation | Credentials | Voice listing |
|---|---|---|---|---|---|
| [`pocket`](pocket.md) | `local` | In-process model | `tts-engine[pocket]` | None | yes |
| [`phonon`](phonon.md) | `local` | In-process model | `tts-engine[phonon]` | None | yes |
| [`qwen3`](qwen3.md) | `local` | In-process model (Apple silicon) | `tts-engine[qwen3]` | None | yes |
| [`kokoro`](kokoro.md) | `local` | In-process model | `tts-engine[kokoro]` | None | yes |
| [`piper`](piper.md) | `local` | In-process model | `tts-engine[piper]` | None | yes |
| [`elevenlabs`](elevenlabs.md) | `hosted` | ElevenLabs cloud API | `tts-engine[elevenlabs]` | API key | yes |
| [`gradium`](gradium.md) | `hosted` | Gradium cloud API | `tts-engine[gradium]` | API key | yes |
| [`mistral`](mistral.md) | `hosted` | Mistral cloud API (Voxtral TTS) | `tts-engine[mistral]` | API key | yes |
| [`deepgram`](deepgram.md) | `hosted` | Deepgram cloud API (Aura-2, Flux TTS) | `tts-engine[deepgram]` | API key | yes |
| [`gemini`](gemini.md) | `hosted` | Google Gemini API (Gemini TTS) | `tts-engine[gemini]` | API key | no |
| [`cartesia`](cartesia.md) | `hosted` | Cartesia cloud API (Sonic) | `tts-engine[cartesia]` | API key | yes |
| [`openai`](openai.md) | `hosted` | OpenAI Realtime API (`gpt-realtime-2.1-mini`) | `tts-engine[openai]` | API key | no |
| [`openai-compatible`](openai-compatible.md) | `hosted` | OpenAI's `/v1/audio/speech` (`gpt-4o-mini-tts`, `tts-1`) or an OpenAI-compatible server | `tts-engine[openai-compatible]` | API key | no |
| [`tone`](tone.md) | `fixture` | Sine tone, no synthesis | Base installation | None | — |
| [`audiofile`](audiofile.md) | `fixture` | Pre-recorded WAV files, no synthesis | Base installation | None | — |

`kind` is the module's class attribute (`REGISTRY[type].kind`): `hosted` and `local` modules are providers and produce real speech, `hosted` from a cloud API (a key and a network), `local` from an in-process model (no network after the first download of its weights). `fixture` modules exist for tests and demos and are never a substitute for a provider. *Voice listing* says whether [`list_voices`](../voices.md) can read the backend's catalog; for a `no`, it raises `VoiceListingUnsupported` with a link to where the voices are documented.

Each module's page has its install line, a minimal config, every field, its voices, its license notes and its troubleshooting. Which backends forward a text stream as it arrives and which wait for whole sentences is in [streaming.md](../streaming.md); how to narrow the list down for an application is in [choosing-a-backend-and-voice.md](../choosing-a-backend-and-voice.md).

## Installing a module

Every provider is an optional extra; the base installation has none, and the two fixtures need none. From a checkout, or for an installed package:

```bash
uv sync --no-dev --extra pocket       # local pocket-tts model (pulls in torch)
uv sync --no-dev --extra phonon       # local Phonon model (no torch)
uv sync --no-dev --extra qwen3        # local Qwen3-TTS model (Apple silicon)
uv sync --no-dev --extra kokoro       # local Kokoro model (no torch)
uv sync --no-dev --extra piper        # local Piper voices (no torch, GPL)
uv sync --no-dev --extra elevenlabs   # ElevenLabs cloud API
uv sync --no-dev --extra gradium      # Gradium cloud API
uv sync --no-dev --extra mistral      # Mistral cloud API (Voxtral TTS)
uv sync --no-dev --extra deepgram     # Deepgram cloud API (Aura-2, Flux)
uv sync --no-dev --extra gemini       # Google Gemini API (Gemini TTS)
uv sync --no-dev --extra cartesia     # Cartesia cloud API (Sonic)
uv sync --no-dev --extra openai       # OpenAI Realtime API
uv sync --no-dev --extra openai-compatible  # OpenAI speech endpoint or an OpenAI-compatible server
uv sync --no-dev --extra mcp          # MCP server (tts-engine-mcp)
uv sync --no-dev --extra hosted-modules  # every hosted provider
uv sync --no-dev --extra all-modules  # every provider, local and hosted
uv sync --no-dev --all-extras         # every extra

# For an installed package:
pip install "tts-engine[hosted-modules]"   # every hosted provider
pip install "tts-engine[pocket]"
pip install "tts-engine[phonon]"
pip install "tts-engine[qwen3]"
pip install "tts-engine[kokoro]"
pip install "tts-engine[piper]"
pip install "tts-engine[elevenlabs]"
pip install "tts-engine[gradium]"
pip install "tts-engine[mistral]"
pip install "tts-engine[deepgram]"
pip install "tts-engine[gemini]"
pip install "tts-engine[cartesia]"
pip install "tts-engine[openai]"
pip install "tts-engine[openai-compatible]"
pip install "tts-engine[mcp,pocket]"
pip install "tts-engine[all-modules]"   # every provider
pip install "tts-engine[all]"   # every extra, the MCP server included
```

Selecting a provider whose extra is not installed fails at engine construction with a `ConfigError` naming the `pip install` command.
