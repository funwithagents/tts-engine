# Documentation

The documentation of `tts-engine`, by what you want to do. The [README](../README.md) is the landing page: what the engine is, how to install it, and a first quick start.

| I want to… | Read |
|---|---|
| speak a text from Python | the README's [quick start](../README.md#quick-start), then [configuration.md](configuration.md): the engine block, loading it from a dict or a JSON file, relative paths, the output device, the output gain |
| pick a backend and a voice | [choosing-a-backend-and-voice.md](choosing-a-backend-and-voice.md): where each one runs, how fast it answers, which voices it has, how to audition them |
| set up one backend | its page under [modules/](modules/index.md): install, config fields, voices, licenses, troubleshooting |
| find a backend's voices from code | [voices.md](voices.md): `list_voices` and `VoiceInfo` |
| speak an LLM response while it is generated | [streaming.md](streaming.md): `say_stream` and what each backend does with a text stream |
| capture the audio, or play it somewhere else | [audio-sinks.md](audio-sinks.md): the default player and custom sinks |
| give an agent a `say` tool, or run the MCP server | [agents-and-mcp.md](agents-and-mcp.md) |
| find out why nothing is heard, or why a backend fails | [troubleshooting.md](troubleshooting.md) |

Beyond the user documentation:

- [specs/](../specs/_index.md) is the normative design: every guarantee, with its rationale, one spec per concept and per backend.
- [examples/](../examples/) has one complete, runnable config per module, in the MCP server's format.
- [benchmarks/latency/](../benchmarks/latency/RESULTS.md) holds the latency measurements and what was concluded from them.
- [tools/voice-audition/](../tools/voice-audition/README.md) is the voice audition tool.
- [AGENTS.md](../AGENTS.md) is the operating manual for working in the repository.
