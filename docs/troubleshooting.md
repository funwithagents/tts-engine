# Troubleshooting

## No audio plays

- Confirm that PortAudio is installed.
- Leave `player.device` as `null` to use the system default, or inspect the available devices with `sounddevice.query_devices()`.
- Remember that audio plays on the machine running `TTSEngine`, not necessarily the machine that initiated an MCP call.

## A provider module requires its extra

A `ConfigError` such as `The 'pocket' module requires the pocket extra` means that provider's libraries are not installed. Run `uv sync --no-dev --extra <name>` (or `pip install "tts-engine[<name>]"`), e.g. `--extra pocket`, `--extra phonon`, `--extra qwen3`, `--extra kokoro`, `--extra piper`, `--extra elevenlabs`, `--extra gradium`, `--extra mistral`, `--extra deepgram`, `--extra gemini`, `--extra cartesia`, `--extra openai`, or `--extra openai-compatible`.

## One voice is louder or quieter than the others

Each backend, model and voice speaks at its own level, and the engine plays that level as it is: two voices can differ by 8 dB or more. Give each engine a `gain_db` in its config to bring them to one level. `tts-engine-level --config engine.json` measures a voice and prints the value to use ([Output gain](configuration.md#output-gain)). Use the same `--target` for every voice you want to match.

## A warning that `gain_db` clipped samples

A warning such as `gain_db +6 dB clipped 412 samples in this utterance; lower gain_db` means a positive `gain_db` pushed the loudest peaks past what 16-bit audio can hold, so they were cut off and may sound harsh. Lower that engine's `gain_db`. Or run `tts-engine-level` again with a lower `--target`: a target below every voice keeps all gains negative, and a negative gain never clips.

## A backend's own errors

Key, voice, model and download errors are specific to each backend, so each module page ends with its own troubleshooting: [`pocket`](modules/pocket.md#troubleshooting), [`phonon`](modules/phonon.md#troubleshooting), [`qwen3`](modules/qwen3.md#troubleshooting), [`kokoro`](modules/kokoro.md#troubleshooting), [`piper`](modules/piper.md#troubleshooting), [`elevenlabs`](modules/elevenlabs.md#troubleshooting), [`gradium`](modules/gradium.md#troubleshooting), [`mistral`](modules/mistral.md#troubleshooting), [`deepgram`](modules/deepgram.md#troubleshooting), [`gemini`](modules/gemini.md#troubleshooting), [`cartesia`](modules/cartesia.md#troubleshooting), [`openai-compatible`](modules/openai-compatible.md#troubleshooting).
