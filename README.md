# tts-engine

A streaming text-to-speech engine for Python. Choose a TTS module, construct a `TTSEngine`, and call `await engine.say(text)`, or `await engine.say_stream(text_stream)` to speak an LLM response while it is still being generated.

Audio is synthesized and consumed chunk by chunk, minimizing the delay before playback begins. Speech comes from a provider module you install as an extra: the ElevenLabs, Gradium, Mistral, Deepgram, Gemini, Cartesia or OpenAI cloud APIs (OpenAI's Realtime API through the `openai` module; its speech endpoint through the `openai-compatible` module, which also reaches any OpenAI-compatible server), or the local pocket-tts, Phonon, Qwen3-TTS, Kokoro or Piper models. There is no default provider. Two built-in fixture modules, `tone` and `audiofile`, stand in for a provider in tests and demos. By default, audio plays on the machine running the engine; applications can instead provide their own audio sink to capture or route the PCM stream.

Agent-friendly tools are included as an optional interface over the same engine, and an MCP server is available through the `mcp` extra.

## Installation

This project requires Python 3.11+ and uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
git clone https://github.com/funwithagents/tts-engine.git
cd tts-engine
uv sync --no-dev
```

`--no-dev` is what keeps that install minimal: `uv sync` includes the `dev` group by default, and `dev` depends on `tts-engine[all]`, so a bare `uv sync` here builds the full contributor environment — every extra, `torch` included (see [Development](#development)).

Default local playback uses `sounddevice`, which requires PortAudio. On Ubuntu:

```bash
sudo apt-get install libportaudio2
```

The base installation contains the engine, tools, audio player, and the `tone`/`audiofile` fixture modules, but no speech provider and no server. Install the provider you want as an extra, and add the `mcp` extra to run the MCP server:

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
uv sync --no-dev --all-extras         # every extra

# For an installed package:
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
pip install "tts-engine[all]"
```

Selecting a provider whose extra is not installed fails at engine construction with a `ConfigError` naming the `pip install` command.

## Quick start

Choose a provider first and install its extra (see [TTS modules](#tts-modules)). This example uses the local pocket-tts provider, which needs no API key (`uv sync --no-dev --extra pocket`). Construct an engine configuration and speak:

```python
import asyncio

from tts_engine import TTSEngine, TTSEngineConfig


async def main() -> None:
    config = TTSEngineConfig(module={"type": "pocket", "voice": "alba"})

    engine = TTSEngine(config)
    await engine.say("Hello from the TTS engine")


asyncio.run(main())
```

No server or protocol is involved. The text is synthesized through the configured module and streamed to the local audio device. Any other module works the same way: only the `module` block changes.

> **No key or extra yet?** Swap the module for `{"type": "tone"}` to check the engine and your audio output end to end. It plays a short sine tone rather than speech. It is a fixture module for tests, not a TTS backend.

## How the engine works

The reusable core has three parts:

```text
text        → TTSEngine.say()        → TTSModule → AudioSink
text stream → TTSEngine.say_stream() ↗                 └─ AudioPlayer by default
```

- `TTSEngine` owns the selected module and audio sink and exposes `say(text)` for a complete text and `say_stream(text_stream)` for text arriving over time.
- A `TTSModule` connects the engine to one synthesis backend. Exactly one module is selected when the engine is constructed.
- An `AudioSink` consumes the streaming PCM chunks. The built-in `AudioPlayer` sends them to the local audio device.

The module declares its output sample rate, and the engine configures the default player to match it. Calls to `say()` and `say_stream()` on the same engine are serialized, and each call returns after the sink has been drained.

An engine holds its module's client or loaded model for its lifetime. Release it with `await engine.close()`, or use the engine as an async context manager; after that, `say()` and `say_stream()` raise `RuntimeError`. The sink is not touched: an injected sink is yours to close.

```python
async with TTSEngine(config) as engine:
    await engine.say("Hello from the TTS engine")
```

## Configuring an engine

`TTSEngine` consumes a `TTSEngineConfig`. Whether it comes from a dictionary, JSON text, or a file, its data has the same **engine-block** shape. `module.type` selects the module and every other `module` field is specific to it (this example uses `pocket`; see [TTS modules](#tts-modules)):

```json
{
  "module": {
    "type": "pocket",
    "voice": "alba"
  },
  "player": {
    "device": null
  }
}
```

Pass that object directly to a loader; do not add an outer `"engine"` key. A larger application can store it under any key it chooses, while the optional MCP server format specifically stores it under `"engine"`.

The [`examples/`](examples/) directory holds one complete MCP-server config per module (`config.pocket.json`, `config.phonon.json`, `config.qwen3.json`, `config.kokoro.json`, `config.piper.json`, `config.elevenlabs.json`, `config.elevenlabs-v4.json`, `config.gradium.json`, `config.mistral.json`, `config.deepgram.json`, `config.deepgram-flux.json`, `config.gemini.json`, `config.cartesia.json`, `config.openai.json`, `config.openai-compatible.json`, `config.tone.json`, `config.audiofile.json`). No single example is the default.

| Source | Constructor | Expected shape |
|---|---|---|
| Python-owned values | `TTSEngineConfig(module=...)` | Constructor arguments; `player` defaults to the system device |
| Dictionary, including one extracted from a larger config | `TTSEngineConfig.from_dict(...)` | Engine block |
| JSON string | `TTSEngineConfig.from_json(...)` | Engine block |
| Standalone engine JSON file | `TTSEngineConfig.from_json_file(...)` | Engine block |

The two common Python patterns are:

```python
from tts_engine import TTSEngine, TTSEngineConfig

# Values owned by this Python code.
config = TTSEngineConfig(module={"type": "pocket", "voice": "alba"})

# Or a validated engine block embedded in a larger application config.
config = TTSEngineConfig.from_dict(application_config["tts"])

engine = TTSEngine(config)
```

**Relative file paths.** Module fields that name files (an `audiofile` WAV, a pocket voice `.wav`) resolve against the module's reserved `base_dir` key. `from_json_file()` fills it in with the config file's own directory, so paths are relative to the file rather than the working directory. `from_dict()` and `from_json()` accept a `base_dir=` keyword for the same purpose. Without one, relative paths resolve against the working directory. An explicit `base_dir` in the module block wins, and a relative one is itself resolved against the loader's directory.

`from_dict()` and the JSON loaders validate the shared structure before the selected module validates and resolves its own fields during engine construction. For example, the ElevenLabs module reads the environment variable named by `api_key_env` at that point.

The optional `player.device` field selects the local output device: `null` uses the system default, a string selects by name, and an integer selects by device index. To inspect available devices:

```bash
uv run python -c "import sounddevice; print(sounddevice.query_devices())"
```

## TTS modules

A module is a swappable synthesis backend. Its `type` selects an entry from the module registry, while every other field in the `module` object is specific to that backend. Changing modules does not change `TTSEngine`, playback, tools, or MCP usage.

| Module | Kind | Execution | Installation | Credentials |
|---|---|---|---|---|
| `pocket` | Provider | Local, in-process model | `tts-engine[pocket]` | None |
| `phonon` | Provider | Local, in-process model | `tts-engine[phonon]` | None |
| `qwen3` | Provider | Local, in-process model (Apple silicon) | `tts-engine[qwen3]` | None |
| `kokoro` | Provider | Local, in-process model | `tts-engine[kokoro]` | None |
| `piper` | Provider | Local, in-process model | `tts-engine[piper]` | None |
| `elevenlabs` | Provider | ElevenLabs cloud API | `tts-engine[elevenlabs]` | API key |
| `gradium` | Provider | Gradium cloud API | `tts-engine[gradium]` | API key |
| `mistral` | Provider | Mistral cloud API (Voxtral TTS) | `tts-engine[mistral]` | API key |
| `deepgram` | Provider | Deepgram cloud API (Aura-2, Flux TTS) | `tts-engine[deepgram]` | API key |
| `gemini` | Provider | Google Gemini API (Gemini TTS) | `tts-engine[gemini]` | API key |
| `cartesia` | Provider | Cartesia cloud API (Sonic) | `tts-engine[cartesia]` | API key |
| `openai` | Provider | OpenAI Realtime API (`gpt-realtime-2.1-mini`) | `tts-engine[openai]` | API key |
| `openai-compatible` | Provider | OpenAI's `/v1/audio/speech` (`gpt-4o-mini-tts`, `tts-1`) or an OpenAI-compatible server | `tts-engine[openai-compatible]` | API key |
| `tone` | Fixture | Sine tone, no synthesis | Base installation | None |
| `audiofile` | Fixture | Pre-recorded WAV files, no synthesis | Base installation | None |

Providers produce real speech. Fixture modules exist for tests and demos and are never a substitute for a provider.

### Finding a voice

`list_voices` returns the voices a backend offers, read from its own catalog. It takes the same `module` block as the engine but needs no voice, so the credentials are enough:

```python
from tts_engine import list_voices

voices = list_voices({"type": "cartesia", "api_key_env": "CARTESIA_API_KEY"})
men = [v for v in voices if v.gender == "male" and "en" in v.languages]
config = {"type": "cartesia", "api_key_env": "CARTESIA_API_KEY", **men[0].config}
```

Each `VoiceInfo` carries an `id`, a `name`, and `config`, the module fields that select it (`{"voice_id": ...}`, `{"voice": ...}`, `{"model": ...}` for Deepgram, `{"voice": ..., "speaker": ...}` for a multi-speaker Piper voice). It also has `languages`, a `gender` normalized to `male` / `female` / `neutral` when the backend gives one, the backend's own `age` wording, a `description` and `tags`.

The call blocks while it reaches the provider or downloads a catalog (qwen3 loads its model), so async code runs it with `asyncio.to_thread`. Gemini, OpenAI Realtime and `/v1/audio/speech` have no listing endpoint: for them it raises `VoiceListingUnsupported`, whose message links to the page that lists their voices. The fixtures list none.

### `pocket`

The pocket module runs [kyutai-labs/pocket-tts](https://github.com/kyutai-labs/pocket-tts) in process. It needs no API key and makes no network request during synthesis after its model weights are available. The first use may download weights into the Hugging Face cache.

Install the optional dependencies:

```bash
uv sync --no-dev --extra pocket
# For an installed package:
pip install "tts-engine[pocket]"
```

Then select the module:

```json
{
  "module": {
    "type": "pocket",
    "voice": "alba",
    "language": "english",
    "device": "auto"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | The model's default voice (`alba` for English, `estelle` for French, …) | Preset name, a local audio file (`.wav`, `.mp3`, `.flac`) or `.safetensors` voice state (relative to `base_dir`), or an `hf://`/`https://` URL to one. Cloning from audio needs the gated weights (below). |
| `language` | no | pocket-tts default (`english`, the `english_2026-10` model) | The model: `english`, `french`, `german`, `italian`, `portuguese`, `spanish`, `dutch`, a `<language>_24l` variant, a dated English model such as `english_2026-09`, or a tiny one: `english_18m_2026-10`, `english_7m_2026-10`, `english_2m_2026-10`. Not together with `config`. |
| `config` | no | — | A custom model config YAML (`hf://…/file.yaml`, `https://…`, or a local path relative to `base_dir`) for community or fine-tuned weights. Preset voices do not work with it; use an audio voice. |
| `device` | no | `auto` | `auto`, `cpu`, `cuda`, or experimental `mps`. `auto` selects CUDA when available and otherwise CPU, and always CPU with `quantize`. |
| `quantize` | no | `false` | Int8 quantization of the transformer: about half the memory and faster on x86 CPUs. CPU only. |
| `temperature` | no | Model default (0.3) | Sampling temperature. |
| `max_tokens` | no | pocket-tts default | Per-text-chunk token cap. Increase only for unusually long unbroken text. |

The module emits mono PCM at the model's native sample rate, 24,000 Hz. On Apple Silicon, `auto` intentionally uses CPU; `mps` remains explicit and experimental.

Preset voices work out of the box. Cloning a voice from an audio file needs the gated weights: accept the terms at [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts), then set `HF_TOKEN` (or run `hf auth login`). Without them, an audio voice is rejected at construction with a `ConfigError`.

### `phonon`

The phonon module runs Gradium's open-weight [Phonon](https://gradium.ai/phonon) models in process through the [`ptts`](https://github.com/gradium-ai/xn-ptts) runtime. It needs no API key and no PyTorch, installs from wheels on Linux, macOS (Apple Silicon and Intel) and Windows, and runs much faster than real time on a CPU. The first use downloads the model (about 160 MB) and any catalog voice into the Hugging Face cache.

Install the optional dependencies:

```bash
uv sync --no-dev --extra phonon
# For an installed package:
pip install "tts-engine[phonon]"
```

Then select the module:

```json
{
  "module": {
    "type": "phonon",
    "voice": "Juniper"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `language` | no | `en` | Text normalization: `en`, `fr`, `de`, `es`, `pt`, or `none`. It does not select the model. |
| `model` | no | `hf://gradium/phonon-31m-en` | `hf://owner/name[@revision]`, or a local model directory (relative to `base_dir`). |
| `voice` | no | The model's `default` voice | A Phonon catalog voice name such as `Juniper`, `Harper` or `Miles`, or a local `.safetensors` voice file (relative to `base_dir`). |
| `device` | no | `auto` | `auto` picks the first device the installed build offers (Metal on Apple Silicon, CPU elsewhere), or `cpu`, `metal`. |
| `quant` | no | Model weights | CPU-only weight format, e.g. `q8_0` or `q4k`. |
| `temperature` | no | `ptts` default (0.3) | Sampling temperature, 0 or more. |
| `cfg_coef` | no | Model default | Guidance coefficient (voice adherence). |

The module emits mono PCM at 24,000 Hz. The default English model is Apache-2.0. The French, German, Spanish, Portuguese and multilingual models (`gradium/phonon-40m-*`) are gated and licensed CC BY-NC 4.0: they need an `HF_TOKEN` with access, plus matching `model` and `language` fields.

### `qwen3`

The qwen3 module runs [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache-2.0) in process through [`qwentts-cpp-python`](https://github.com/andimarafioti/qwentts-cpp-python), bindings over the qwentts.cpp GGML engine. It needs no API key and no PyTorch, speaks ten languages with nine preset voices, and can design a voice from a text description. On an Apple-silicon Mac it runs on Metal at about 3–4× real time, with first audio in about 0.2 s. The first use downloads the GGUF weights into the Hugging Face cache: about 1.3 GB for the default 0.6B model at `Q8_0`, about 2.3 GB for a 1.7B model.

The extra installs on **Apple-silicon macOS 14+ only**, because PyPI has no CPU or Windows build of the runtime. On Linux with an NVIDIA GPU, glibc 2.39+ and the CUDA 12.8 runtime, install the CUDA wheel yourself (`pip install "qwentts-cpp-python>=0.5,<0.6"`); that path is not tested here.

```bash
uv sync --no-dev --extra qwen3
# For an installed package:
pip install "tts-engine[qwen3]"
```

Then select the module:

```json
{
  "module": {
    "type": "qwen3",
    "voice": "ryan",
    "language": "english"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `model` | no | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | `Qwen/Qwen3-TTS-12Hz-{0.6B,1.7B}-CustomVoice` (preset voices) or `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` (a voice described in `instruct`). The `-Base` cloning models are not supported. |
| `quant` | no | `Q8_0` | Weight format: `Q8_0`, `Q4_K_M`, `BF16` or `F32`. |
| `voice` | no | `aiden` | A preset voice: `aiden`, `ryan`, `vivian`, `serena`, `uncle_fu`, `dylan`, `eric`, `ono_anna`, `sohee` (case-insensitive). Not allowed with a VoiceDesign model. |
| `instruct` | VoiceDesign only | None | Style instruction for CustomVoice ("Speak cheerfully."), or the voice description for VoiceDesign. Qwen documents it for the 1.7B models. |
| `language` | no | `auto` | `auto` or `english`, `chinese`, `french`, `german`, `spanish`, `italian`, `portuguese`, `russian`, `japanese`, `korean`. |
| `temperature` | no | Library default (0.9) | Sampling temperature, greater than 0. |

The module emits mono PCM at 24,000 Hz and synthesizes one sentence per call, for a complete text as well as a text stream.

### `kokoro`

The kokoro module runs [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0) in process through [`kokoro-onnx`](https://github.com/thewh1teagle/kokoro-onnx): onnxruntime for the model, and an espeak-ng that ships with the package for pronunciation. It needs no API key and no PyTorch, and it has 54 voices in nine languages: American and British English, Spanish, French, Hindi, Italian, Japanese, Brazilian Portuguese and Mandarin. On an Apple M5 Pro CPU it runs at about 4–5× real time, with first audio in about 0.4 s. The first use downloads about 350 MB into the Hugging Face cache. It installs on Linux (x86_64 and arm64), Apple-silicon macOS and Windows, but not on Intel Macs, which have no onnxruntime wheel.

It uses `kokoro-onnx` rather than hexgrad's official `kokoro` package because the official package needs PyTorch and does not support Python 3.13. The trade-off is English pronunciation: `kokoro-onnx` uses espeak-ng directly instead of the official package's misaki dictionary.

```bash
uv sync --no-dev --extra kokoro
# For an installed package:
pip install "tts-engine[kokoro]"
```

Then select the module:

```json
{
  "module": {
    "type": "kokoro",
    "voice": "af_heart"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | `af_heart` | A voice from the voices file. The first letter is the language (`a` American English, `b` British English, `e` Spanish, `f` French, `h` Hindi, `i` Italian, `j` Japanese, `p` Brazilian Portuguese, `z` Mandarin), the second the gender: `af_heart`, `am_michael`, `bf_emma`, `ff_siwis`, … The full list is in [Kokoro's VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). |
| `language` | no | From the voice's first letter | An espeak-ng language code: `en-us`, `en-gb`, `es`, `fr-fr`, `hi`, `it`, `ja`, `pt-br`, `cmn`. |
| `speed` | no | Library default (1.0) | Speaking rate, from 0.5 to 2.0. |
| `model` | no | `kokoro-v1.0.onnx`, pinned | `hf://owner/repo/path/file.onnx[@revision]`, or a local path (relative to `base_dir`), e.g. the int8 or fp16 exports from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases). |
| `voices` | no | `voices-v1.0.bin`, pinned | Same forms as `model`, for the voices file. |

The default files are kokoro-onnx's own release files, from the `fastrtc/kokoro-onnx` Hugging Face repository pinned to one commit.

The module emits mono PCM at 24,000 Hz. It synthesizes one sentence per call, for a complete text as well as a text stream, and synthesizes the next sentence while the current one plays, so there is no gap between sentences.

**License note.** The model, `kokoro-onnx` and onnxruntime are permissively licensed, but the pronunciation step is not: `phonemizer` is GPLv3+ and the bundled espeak-ng is GPL-3.0. The official `kokoro` package uses espeak-ng too. If you redistribute an application that includes this extra, check what the GPL requires of you.

### `piper`

The piper module runs [Piper](https://github.com/OHF-Voice/piper1-gpl) voices in process through the `piper-tts` package: small VITS models on onnxruntime, with espeak-ng built in. It needs no API key and no PyTorch, and it is the fastest local module: on an Apple M5 Pro CPU the default voice makes about 50 seconds of audio per second, with first audio in well under 0.1 s, and Piper runs in real time on a Raspberry Pi. There are 178 voices in 58 locales in [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) ([samples](https://rhasspy.github.io/piper-samples/)). They sound less natural than Kokoro. The first use of a voice downloads it into the Hugging Face cache (63 MB for the default). It installs on Linux (x86_64 and arm64), Apple-silicon macOS and Windows, but not on Intel Macs, which have no onnxruntime wheel.

```bash
uv sync --no-dev --extra piper
# For an installed package:
pip install "tts-engine[piper]"
```

Then select the module:

```json
{
  "module": {
    "type": "piper",
    "voice": "en_US-ljspeech-medium"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `voice` | no | `en_US-ljspeech-medium` | A voice key from rhasspy/piper-voices, `<locale>-<name>-<quality>`: `en_GB-alba-medium`, `fr_FR-siwis-medium`, `de_DE-thorsten-high`, … Not with `model`. |
| `model` | no | None | A voice outside the catalog: `hf://owner/repo/path/voice.onnx[@revision]` or a local `.onnx` path (relative to `base_dir`), with its `.onnx.json` next to it. Not with `voice`. |
| `speaker` | no | The voice's default | For a multi-speaker voice (`en_US-libritts_r-medium`, `en_US-arctic-medium`): a speaker name or index. |
| `speed` | no | The voice's own pace | Speaking rate, from 0.5 to 2.0. |

Catalog voices are pinned to one commit of the repository. The module emits mono PCM at the voice's rate: 22,050 Hz for `medium` and `high` voices, 16,000 Hz for `low` ones. It synthesizes one sentence at a time and prepares the next one while the current one plays. The Japanese, Thai and two of the Chinese voices (`zh_CN-chaowen-medium`, `zh_CN-xiao_ya-medium`) need extra packages and are not supported; the module says so when it loads them.

**Voice licenses.** Each voice is trained on a dataset with its own license, named in the voice's `MODEL_CARD` on Hugging Face. Read it before you ship a voice. For example, `en_US-ljspeech-*` (the default), `bryce`, `john`, `kristin` and `norman` are public domain; `en_US-libritts_r-medium`, `en_GB-alba-medium` and `fr_FR-siwis-medium` are CC BY 4.0 (credit required). The popular `en_US-lessac-*` voices come from a dataset under a *research* license.

**License note.** `piper-tts` is GPL-3.0-or-later, with espeak-ng (GPL-3.0) built in. Using it, locally or on a server, is not restricted. If you redistribute an application that includes this extra, check what the GPL requires of you.

### `elevenlabs`

The ElevenLabs module streams MP3 from the ElevenLabs API and decodes it to signed 16-bit PCM in process before passing it to the audio sink. Every model works for both a complete text and a text stream: a text stream goes over the text-to-speech WebSocket with a v2 model, or over the text-to-dialogue WebSocket with a v3/v4 model such as `eleven_v4_turbo` (see [`examples/config.elevenlabs-v4.json`](examples/config.elevenlabs-v4.json)). Install it with the `elevenlabs` extra:

```bash
uv sync --no-dev --extra elevenlabs
# For an installed package:
pip install "tts-engine[elevenlabs]"
```

Prefer referencing an environment variable so secrets do not appear in configuration files:

```json
{
  "module": {
    "type": "elevenlabs",
    "api_key_env": "ELEVENLABS_API_KEY",
    "voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "model": "eleven_flash_v2_5",
    "stability": 0.5,
    "similarity_boost": 0.75
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | ElevenLabs voice ID. |
| `model` | no | `eleven_flash_v2_5` | Model ID. `eleven_flash_v2_5` favors latency; `eleven_multilingual_v2` favors quality; `eleven_v4_turbo`, `eleven_v4` and `eleven_v3*` stream over the text-to-dialogue socket, which holds a dialogue session from a separate, smaller pool. |
| `stability` | no | `0.5` | Voice stability from 0.0 to 1.0. |
| `similarity_boost` | no | `0.75` | Similarity boost from 0.0 to 1.0. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at 44,100 Hz.

### `gradium`

The Gradium module streams raw PCM from the [Gradium](https://gradium.ai) API over a WebSocket, so no decoding happens in process. Install it with the `gradium` extra:

```bash
uv sync --no-dev --extra gradium
# For an installed package:
pip install "tts-engine[gradium]"
```

Prefer referencing an environment variable so secrets do not appear in configuration files:

```json
{
  "module": {
    "type": "gradium",
    "api_key_env": "GRADIUM_API_KEY",
    "voice_id": "91EdXxJDbWICDBgz",
    "model": "default",
    "sample_rate": 48000
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | Gradium voice ID, from the flagship catalog or a custom voice. |
| `model` | no | `default` | Model alias: `default` or `gradium-tts-beta`. |
| `sample_rate` | no | `48000` | Output rate: `8000`, `16000`, `24000`, `44100`, or `48000`. `48000` is native; other rates are resampled by the API. |
| `temp` | no | API default (0.7) | Sampling temperature from 0.0 to 1.4. |
| `cfg_coef` | no | API default (2.0) | Voice similarity from 1.0 to 4.0. |
| `padding_bonus` | no | API default (0.0) | Speech speed from -4.0 to 4.0; negative is faster. |
| `rewrite_rules` | no | the voice's language | Text-rewriting rules applied before synthesis; `none` disables them. |
| `pronunciation_id` | no | — | Pronunciation dictionary ID. |
| `base_url` | no | SDK default | API base URL, for a regional or self-hosted endpoint. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. Voice-setting fields are sent only when set, so the API's own defaults apply otherwise.

### `mistral`

The Mistral module streams speech from Mistral's hosted Voxtral TTS models through the official `mistralai` SDK. Install it with the `mistral` extra:

```bash
uv sync --no-dev --extra mistral
# For an installed package:
pip install "tts-engine[mistral]"
```

```json
{
  "module": {
    "type": "mistral",
    "api_key_env": "MISTRAL_API_KEY",
    "voice_id": "01a14675-dc30-778a-8d01-b5dead5a522a"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | A preset voice's ID or slug (`client.audio.voices.list()` lists them), or a custom voice's ID. The API has no default voice. |
| `model` | no | `voxtral-mini-tts-3-0` | Mistral TTS model ID. |
| `expressiveness` | no | API default | Speaker guidance scale, a finite number of at least 1.0. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |
| `server_url` | no | SDK default | API base URL, for a regional or self-hosted endpoint. |

The module emits mono PCM at 24,000 Hz. Text is sent verbatim, so the model's inline emotion and pace tags pass through. A text stream is synthesized one request per sentence.

### `deepgram`

The Deepgram module streams speech from Deepgram's hosted voices over the official `deepgram-sdk` WebSockets: the Aura-2 voices, or the newer Flux TTS voices when `model` starts with `flux-` (e.g. `flux-haley-en`; see [`examples/config.deepgram-flux.json`](examples/config.deepgram-flux.json)). Install it with the `deepgram` extra:

```bash
uv sync --no-dev --extra deepgram
# For an installed package:
pip install "tts-engine[deepgram]"
```

```json
{
  "module": {
    "type": "deepgram",
    "api_key_env": "DEEPGRAM_API_KEY",
    "model": "aura-2-thalia-en"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `model` | no | `aura-2-thalia-en` | The model, which is also the voice: an Aura model, `aura-2-<voice>-<language>` (e.g. `aura-2-hector-fr`), or a Flux model, `flux-<voice>-<language>` (e.g. `flux-haley-en`). |
| `sample_rate` | no | `24000` | Output rate: `8000`, `16000`, `24000`, `32000` or `48000`. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier: 0.7 to 1.5 with an Aura model; 0.5 to 1.5 in steps of 0.05 with a Flux model. |
| `expressivity` | no | API default | Flux models only: how expressive the delivery is, an integer from -2 to 2. |
| `mip_opt_out` | no | API default | `true` opts out of Deepgram's Model Improvement Program. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. A text stream goes to the socket as it arrives, and a complete text in small slices. Text is handed to the server in flushed segments, one at a time, and only while the audio already received runs less than about ten seconds ahead of playback, so the server never holds more unspoken text than it accepts and a long reply does not pile up audio in memory.

### `gemini`

The Gemini module streams speech from Google's Gemini TTS models through the official `google-genai` SDK and the Gemini Developer API. Install it with the `gemini` extra:

```bash
uv sync --no-dev --extra gemini
# For an installed package:
pip install "tts-engine[gemini]"
```

```json
{
  "module": {
    "type": "gemini",
    "api_key_env": "GEMINI_API_KEY",
    "voice": "Kore"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice` | no | API default | A prebuilt voice name, e.g. `Kore`, `Puck`, `Charon` or `Zephyr`. |
| `model` | no | `gemini-3.8-flash-tts` | Gemini TTS model ID, e.g. `gemini-3.8-flash-lite-tts`. |
| `language_code` | no | detected from the text | BCP-47 language code, e.g. `en-US` or `fr-FR`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at 24,000 Hz. Text is sent verbatim as the prompt, so natural-language direction ("Say cheerfully: …") and inline tags pass through. A text stream is synthesized one request per sentence, which has two limitations:

- Each sentence starts after a short pause (median ~0.8 s, up to ~1 s), the time to first audio of one request.
- The Gemini API limits requests per model per minute (10 on Tier 1). A text stream of more than ~10 sentences within a minute fails partway with a `TTSError` reporting `429 RESOURCE_EXHAUSTED`; a higher usage tier raises the limit.

### `cartesia`

The Cartesia module streams speech from Cartesia's Sonic models through the official `cartesia` SDK and its realtime WebSocket. Install it with the `cartesia` extra:

```bash
uv sync --no-dev --extra cartesia
# For an installed package:
pip install "tts-engine[cartesia]"
```

```json
{
  "module": {
    "type": "cartesia",
    "api_key_env": "CARTESIA_API_KEY",
    "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `voice_id` | yes | — | Cartesia voice ID (a UUID from the voice library or a cloned voice); the example is "Tessa", an English voice. |
| `model` | no | `sonic-3.6` | Sonic model ID, e.g. `sonic-3`, a dated snapshot, or `sonic-latest`. |
| `sample_rate` | no | `24000` | Output rate: `8000`, `16000`, `22050`, `24000`, `44100`, or `48000`. |
| `language` | no | API default | Language code of the text, e.g. `en` or `fr`. |
| `speed` | no | API default | Speaking-rate multiplier, `0.6`–`1.5`. |
| `volume` | no | API default | Volume multiplier, `0.5`–`2.0`. |
| `emotion` | no | neutral | An emotion name such as `happy`, `calm` or `sympathetic`. |
| `max_buffer_delay_ms` | no | API default (3000) | How long the server may wait for more text before speaking a fragment with no sentence end, `0`–`5000`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at the configured `sample_rate`. A text stream goes to the socket as it arrives, and a complete text in small slices. Text is handed to the server one segment per Cartesia context, the next context opened once the server has acknowledged the previous one and only while the audio already received runs less than about ten seconds ahead of playback. If the text source pauses for several seconds, the module ends the idle context and continues on a fresh one, so the rest of the text is still spoken.

### `openai`

The OpenAI module speaks through OpenAI's Realtime API (`gpt-realtime-2.1-mini`), the successor of the speech models OpenAI shuts down on 2027-01-06, over the official `openai` SDK's realtime WebSocket. Install it with the `openai` extra:

```bash
uv sync --no-dev --extra openai
# For an installed package:
pip install "tts-engine[openai]"
```

```json
{
  "module": {
    "type": "openai",
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. |
| `model` | no | `gpt-realtime-2.1-mini` | Realtime model ID, e.g. `gpt-realtime-2.1`. |
| `voice` | no | `marin` | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin` or `cedar`. |
| `instructions` | no | — | Voice direction such as tone, pace or accent, added to the module's instruction to read the text verbatim. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier, `0.25`–`1.5`. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails. |

The module emits mono PCM at 24,000 Hz. The Realtime API is a conversation model, so the module asks it to read each text aloud word for word. In testing it did, including for questions and instructions inside the text, but nothing guarantees it. Each utterance uses one connection: a long text is sent in parts, and a text stream is spoken one sentence at a time. In testing, the first audio arrived after 1.2–1.7 s, and each later sentence 0.5–1.2 s after the previous one. Billing is per audio token, about $0.025 per minute of speech with `gpt-realtime-2.1-mini`.

### `openai-compatible`

The OpenAI-compatible module streams speech from a `/v1/audio/speech` endpoint through the official `openai` SDK: OpenAI's own by default, or, with `base_url`, any OpenAI-compatible speech server that returns raw `pcm` (vLLM-Omni, Kokoro-FastAPI, `mlx_audio.server`, …). Install it with the `openai-compatible` extra:

```bash
uv sync --no-dev --extra openai-compatible
# For an installed package:
pip install "tts-engine[openai-compatible]"
```

```json
{
  "module": {
    "type": "openai-compatible",
    "api_key_env": "OPENAI_API_KEY",
    "voice": "marin"
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `api_key_env` | one of `api_key_env` or `api_key` | — | Name of the environment variable holding the API key. Recommended for file-based configuration. |
| `api_key` | one of `api_key_env` or `api_key` | — | Literal API key. A non-empty literal key takes precedence over `api_key_env`. For a compatible server that needs no key, use any placeholder, e.g. `"unused"`. |
| `voice` | yes | — | A built-in voice: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin` or `cedar`, or a compatible server's voice name. |
| `model` | no | `gpt-4o-mini-tts-2025-03-20` | Speech model ID, e.g. `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd`, or a compatible server's model. The default snapshot is pinned because the `gpt-4o-mini-tts` alias's latency is erratic. |
| `instructions` | no | — | Voice direction such as tone, pace or accent. Used by `gpt-4o-mini-tts`; ignored by `tts-1` and `tts-1-hd`. |
| `speed` | no | API default (1.0) | Speaking-rate multiplier, `0.25`–`4.0`. |
| `base_url` | no | OpenAI | Base URL of an OpenAI-compatible server, including `/v1`, e.g. `http://localhost:8000/v1`. |
| `sample_rate` | no | `24000` | The rate of the server's `pcm` output. OpenAI's is always 24,000 Hz; set it only with `base_url`, to match the server. |
| `timeout` | no | `30` | Seconds to wait for the provider before a stalled request fails; also how long a cancel issued during such a stall can take. |

The module emits mono PCM at `sample_rate`. A text stream is synthesized one request per sentence. OpenAI's time to first audio varies: `tts-1` takes about 1.2 s, while the `gpt-4o-mini-tts` alias took from under 1 s to about 19 s in testing; the default `2025-03-20` snapshot answers in under 1 s. OpenAI has announced the shutdown of these speech models for 2027-01-06; after that date the module serves compatible servers only. Their successor, OpenAI's Realtime API, is the [`openai`](#openai) module.

### `tone`

A fixture module for tests and demos, not a TTS backend. For any text it plays a sine tone whose length is proportional to the text length. It needs no key, no extra, no model, and no files, and it is deterministic. That makes it a good module for an integration test suite, or for checking audio output.

```json
{ "module": { "type": "tone" } }
```

| Field | Required | Default | Description |
|---|---|---|---|
| `sample_rate` | no | `24000` | Output rate in Hz. |
| `frequency` | no | `440.0` | Sine frequency in Hz. |
| `seconds_per_char` | no | `0.05` | Tone duration is `len(text) * seconds_per_char` seconds. |
| `amplitude` | no | `0.2` | Peak amplitude, in (0, 1]. |

### `audiofile`

A fixture module for tests and demos, not a TTS backend. It maps known sentences to pre-recorded WAV files and plays the matching file, falling back to `default_file` when no entry matches:

```json
{
  "module": {
    "type": "audiofile",
    "default_file": "fixtures/default.wav",
    "files": [
      { "text": "Hello, world!", "file": "fixtures/hello.wav" },
      { "text": "Goodbye.", "file": "fixtures/goodbye.wav" }
    ]
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `files` | one of `files` or `default_file` | `[]` | List of `{"text", "file"}` entries. Matching is exact and case-sensitive after collapsing whitespace. |
| `default_file` | one of `files` or `default_file` | — | WAV played when no entry matches. Without it, an unmatched text raises a TTS error. |

Paths are absolute or relative to `base_dir` (the config file's directory when loaded from a file). Every file must be a 16-bit mono WAV, and all files must share one sample rate, which becomes the module's rate. Files are checked when the engine is constructed.

### Add a module

To add another synthesis backend:

1. Create a package `src/tts_engine/modules/<name>/` (one folder per backend) with the class extending `TTSModule` from [`src/tts_engine/modules/base.py`](src/tts_engine/modules/base.py) in `module.py`, re-exported from its `__init__.py`. A provider with third-party dependencies should put them behind its own packaging extra and import them inside `__init__`, raising `ConfigError` when they are missing. Resolve file-path fields with `resolve_path(config, value)`.
2. Implement `sample_rate`, `async synthesize_text(text, options, callback)`, `async synthesize_text_stream(text_stream, options, callback)` and the `list_voices(config)` classmethod (the backend's voices as `VoiceInfo`, or `VoiceListingUnsupported` naming where they are documented). The callback receives signed 16-bit little-endian mono PCM chunks. For the text stream, a backend with an incremental API forwards the pieces as they arrive; one that needs a whole prompt groups them into sentences with `segment_sentences` and synthesizes each sentence as soon as it is complete (see `tone`, `audiofile` and `pocket` for the shapes, and `run_text_stream_worker` in `base.py` for the async-source-to-worker-thread bridge).
3. Register the class under a type name in [`src/tts_engine/modules/__init__.py`](src/tts_engine/modules/__init__.py).
4. Select it with `{"module": {"type": "your-module", ...}}` in the engine configuration.

The engine and its integrations require no backend-specific changes.

## Speak an LLM response as it streams

`say_stream` takes an async iterable of text pieces and starts playing before the text is complete, so an LLM answer is heard as it is written rather than after the last token:

```python
async def llm_tokens():
    async with llm_client.messages.stream(...) as stream:  # any async token source
        async for token in stream.text_stream:
            yield token


await engine.say_stream(llm_tokens())
```

Pieces are concatenated verbatim (tokens carry their own spaces). The ElevenLabs, Gradium, Deepgram and Cartesia modules forward the text to their realtime sockets as it arrives; `pocket`, `phonon`, `qwen3`, `kokoro`, `piper`, `mistral`, `gemini`, `openai`, `openai-compatible` and `audiofile` synthesize sentence by sentence as each one completes; `tone` plays a tone per piece. A plain list is accepted for in-memory sequences, and a bare string is rejected. If the text source raises, playback stops and that exception propagates unchanged. A long pause in the source is heard as silence, not an error.

## Audio output and custom sinks

Without a custom sink, `TTSEngine` constructs an `AudioPlayer` using `player.device` and the active module's sample rate. Playback starts lazily when the first PCM chunk arrives.

Applications that need to capture audio, send it across a network, or feed another playback system can inject an `AudioSink`-compatible object:

```python
class CaptureSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def drain(self) -> None:
        pass


sink = CaptureSink()
engine = TTSEngine(config, sink=sink)
await engine.say("Capture this instead of playing it")
```

The sink receives signed 16-bit little-endian mono PCM at `engine.sample_rate`. `feed()` calls are sequential but may happen off the event-loop thread. `drain()` is called once after every utterance, including when synthesis fails or is cancelled. When a custom sink is supplied, the engine does not construct or use the local audio player.

## Optional integrations

The tools and MCP layers adapt the engine for agents and remote clients. They do not implement synthesis themselves.

```text
MCP client → MCP server → TTSTools → TTSEngine → TTSModule → AudioSink
                         ↑
                non-MCP agent
```

### Use `TTSTools` in an agent

`TTSTools` binds an engine and exposes `say(text) -> str` as an agent-friendly method. It returns `"OK"` on success and converts empty input or expected synthesis failures into a `"TTS error: ..."` result.

```python
from tts_engine import TTSEngine, TTSEngineConfig, TTSTools

config = TTSEngineConfig.from_json_file("tts.json")
engine = TTSEngine(config)
tools = TTSTools(engine)

# Register this bound method directly with an agent framework.
say_tool = tools.say

result = await say_tool("Hello from my agent")
```

Call `engine.say()` directly when you want the raw exception-raising engine contract instead.

### Run the MCP server

The MCP server exposes `TTSTools.say` over StreamableHTTP. It needs the `mcp` extra (`uv sync --no-dev --extra mcp`, or `pip install "tts-engine[mcp]"`) plus the extra for your provider. Without it, `tts-engine-mcp` exits with `tts-engine-mcp requires the mcp extra: pip install tts-engine[mcp]`.

Its configuration wraps the same engine block documented above with an optional `server` block:

```json
{
  "engine": {
    "module": {
      "type": "pocket",
      "voice": "alba"
    },
    "player": {
      "device": null
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  }
}
```

Start from the example for your module in [`examples/`](examples/), then run:

```bash
cp examples/config.pocket.json config.json   # or config.elevenlabs.json, …
uv run tts-engine-mcp --config config.json

# Or, with no provider installed, try the transport with the tone fixture module:
uv run tts-engine-mcp --config examples/config.tone.json
```

The `server` block defaults to `127.0.0.1:8000` when omitted. Connect an MCP client to `http://127.0.0.1:8000/mcp`. On shutdown (Ctrl-C), the server closes the engine, releasing the provider's client or the local model. Server log verbosity is controlled by the `--log-level` command-line flag rather than the JSON configuration:

```bash
uv run tts-engine-mcp --config config.json --log-level DEBUG
```

Library code can also reuse the engine block from an MCP server configuration. `MCPServerConfig` does not need the `mcp` extra:

```python
from tts_engine import MCPServerConfig, TTSEngine

mcp_config = MCPServerConfig.from_json_file("config.json")
engine = TTSEngine(mcp_config.engine)
```

## Troubleshooting

### No audio plays

- Confirm that PortAudio is installed.
- Leave `player.device` as `null` to use the system default, or inspect the available devices with `sounddevice.query_devices()`.
- Remember that audio plays on the machine running `TTSEngine`, not necessarily the machine that initiated an MCP call.

### A provider module requires its extra

A `ConfigError` such as `The 'pocket' module requires the pocket extra` means that provider's libraries are not installed. Run `uv sync --no-dev --extra <name>` (or `pip install "tts-engine[<name>]"`), e.g. `--extra pocket`, `--extra phonon`, `--extra qwen3`, `--extra kokoro`, `--extra piper`, `--extra elevenlabs`, `--extra gradium`, `--extra mistral`, `--extra deepgram`, `--extra gemini`, `--extra cartesia`, `--extra openai`, or `--extra openai-compatible`.

### pocket-tts

- Ensure the `pocket` extra is installed.
- Allow network access and sufficient disk space when model weights are downloaded for the first time.
- On Apple Silicon, keep `device` set to `auto` or `cpu` unless explicitly testing the experimental `mps` path.
- A `ConfigError` starting with `Pocket module:` is pocket-tts rejecting a value: an unknown `language`, a missing file, a preset voice with a custom `config`, or an audio voice without the gated cloning weights.

### Phonon

- Ensure the `phonon` extra is installed.
- Allow network access when the model or a catalog voice is downloaded for the first time; a `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable or a gated model was requested without an `HF_TOKEN`.
- A `ConfigError` naming a device or `quant` means the installed `ptts` build does not support that combination. Quantization works on CPU only.

### Qwen3-TTS

- Ensure the `qwen3` extra is installed, on an Apple-silicon Mac (on any other platform the extra installs nothing and the module reports it missing).
- Allow network access and several gigabytes of disk for the first download; a `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable, or, on Linux, that the CUDA runtime is missing.
- A `ConfigError` listing voices or languages names the values the loaded model accepts.

### Kokoro

- Ensure the `kokoro` extra is installed. It does not install on Intel Macs or on Python 3.14, because `kokoro-onnx` does not support them yet.
- Allow network access for the first download (about 350 MB). A `TTSError` reporting a model load failure usually means the Hugging Face Hub was unreachable and the files are not cached yet.
- A `ConfigError` naming `voice` means the voice is not in the voices file. A `ConfigError` naming `language` means espeak-ng does not know that code. A custom voice whose first letter is not a known language needs an explicit `language`.

### Piper

- Ensure the `piper` extra is installed. It does not install on Intel Macs.
- A `ConfigError` starting with `Piper module: 404` means the `voice` key is not in the catalog; check the spelling against [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).
- A `TTSError` reporting a model load failure on first use usually means the Hugging Face Hub was unreachable and the voice is not cached yet.
- A `ConfigError` naming `speaker` lists how many speakers the voice has; a single-speaker voice takes no `speaker`.

### ElevenLabs

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- Verify that `voice_id` exists and is available to the configured ElevenLabs account.

### Gradium

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- Verify that `voice_id` exists and is available to the configured Gradium account.
- A `TTSError` reporting a sample-rate mismatch means the API answered at a rate other than the configured `sample_rate`; check the value against the supported list.

### Mistral

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` containing `invalid_voice` means `voice_id` is not a preset or custom voice visible to the account; one containing `invalid_model` means the `model` ID is wrong.

### Deepgram

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting HTTP 401 means the key was rejected; HTTP 403 usually means the `model` does not exist or the account cannot use it; HTTP 400 means a value such as `speed` is out of range. A Flux model may also reject a particular `speed` for its voice (`SPEED_NOT_SUPPORTED`).

### Gemini

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` containing `No matching speaker voice` means `voice` is not a prebuilt voice; `NOT_FOUND` means the `model` ID is wrong; `Invalid language_code` means `language_code` is not a BCP-47 code the API accepts.
- A `TTSError` containing `429 RESOURCE_EXHAUSTED` means the per-minute request quota of the model is spent (one request per sentence): wait a minute, shorten the text, or move the Google account to a higher tier.
- A `TTSError` reporting that Gemini stopped synthesis early or blocked the prompt carries the API's reason, e.g. `SAFETY`.

### Cartesia

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting HTTP 401 means the key was rejected; `Voice not found` or `Model not found` (status 404) means `voice_id` or `model` is wrong.
- An unknown `emotion` is not rejected by the API: speech is produced without it.

### OpenAI-compatible

- Verify that the environment variable named by `api_key_env` is set in the process running the engine.
- A `TTSError` reporting `Error code: 401` means the key was rejected; `Error code: 400` with `Invalid value` lists the supported voices; `model_not_found` (404) means `model` is wrong.
- `OpenAI-compatible server returned WAV, not raw pcm` means the server behind `base_url` ignores `response_format: "pcm"`. That server cannot be used with this module.
- If speech from a compatible server sounds too fast, too slow or pitch-shifted, set `sample_rate` to the server's output rate.

## Latency benchmark

`benchmarks/latency/` measures time to first audio, time to first voice, stalls and real-time factor for every module and model through the public `TTSEngine` API, with no audio hardware. The committed result of the development Mac is `benchmarks/latency/results/20261027-1453_mac-m5-pro/` (its `summary.md`), the findings in `benchmarks/latency/RESULTS.md`, and the how-to in its README. It is not run by CI.

## Voice audition

`tools/voice-audition/` helps choose a voice: it renders candidate voices from any backends saying the same sentence, measures each one's time to first audio, and writes a page to listen to them one by one and mark each Yes, Maybe or No. It runs from a checkout and is not installed with the package.

```bash
# An audition file from the catalogs: English male or neutral voices, 8 per backend
uv run python tools/voice-audition/audition.py new my-assistant.json \
    --text "Hello! What would you like to talk about today?" \
    --module phonon --module cartesia --module gradium \
    --gender male --gender neutral --language en --limit 8 --keep-unknown

# Render every candidate (a warm-up, then 3 measured runs) and write the page
uv run python tools/voice-audition/audition.py render my-assistant.json
open my-assistant.audition/index.html
```

The audition file is plain JSON: one `text` and a list of candidates, each with an `id` and a complete module `config`, so it can be written or edited by hand (see `tools/voice-audition/examples/demo.json`). Rendering again only renders what changed. A candidate whose API key or extra is missing is skipped, not failed. The page keeps your choices in the browser, shows the Yes and Maybe voices in their own tabs, and exports and imports the choices as JSON. Details: [specs/voice-audition.md](specs/voice-audition.md).

## Development

Install development dependencies and run all default checks:

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check src tests tests-e2e benchmarks tools
uv run pyright
uv run pytest
```

The `dev` group depends on `tts-engine[all]`, so `uv sync --dev` installs every extra (including `pocket`, which pulls in `torch`) and the whole suite runs with nothing skipped for a missing extra. The default test run is fast and does not use the network. Live tests against real providers and audio hardware are under `tests-e2e/` and must be invoked explicitly. Their `tone` and `audiofile` rows need no key or extra and never skip.
