# Troubleshooting

## No audio plays

- Confirm that PortAudio is installed.
- Leave `player.device` as `null` to use the system default, or inspect the available devices with `sounddevice.query_devices()`.
- Remember that audio plays on the machine running `TTSEngine`, not necessarily the machine that initiated an MCP call.

## A provider module requires its extra

A `ConfigError` such as `The 'pocket' module requires the pocket extra` means that provider's libraries are not installed. Run `uv sync --no-dev --extra <name>` (or `pip install "tts-engine[<name>]"`), e.g. `--extra pocket`, `--extra phonon`, `--extra qwen3`, `--extra kokoro`, `--extra piper`, `--extra elevenlabs`, `--extra gradium`, `--extra mistral`, `--extra deepgram`, `--extra gemini`, `--extra cartesia`, `--extra openai`, or `--extra openai-compatible`.

## A backend's own errors

Key, voice, model and download errors are specific to each backend, so each module page ends with its own troubleshooting: [`pocket`](modules/pocket.md#troubleshooting), [`phonon`](modules/phonon.md#troubleshooting), [`qwen3`](modules/qwen3.md#troubleshooting), [`kokoro`](modules/kokoro.md#troubleshooting), [`piper`](modules/piper.md#troubleshooting), [`elevenlabs`](modules/elevenlabs.md#troubleshooting), [`gradium`](modules/gradium.md#troubleshooting), [`mistral`](modules/mistral.md#troubleshooting), [`deepgram`](modules/deepgram.md#troubleshooting), [`gemini`](modules/gemini.md#troubleshooting), [`cartesia`](modules/cartesia.md#troubleshooting), [`openai-compatible`](modules/openai-compatible.md#troubleshooting).
