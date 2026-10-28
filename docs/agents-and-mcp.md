# Agents and the MCP server

The tools and MCP layers adapt the engine for agents and remote clients. They do not implement synthesis themselves.

```text
MCP client → MCP server → TTSTools → TTSEngine → TTSModule → AudioSink
                         ↑
                non-MCP agent
```

## Use `TTSTools` in an agent

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

## Run the MCP server

The MCP server exposes `TTSTools.say` over StreamableHTTP. It needs the `mcp` extra (`uv sync --no-dev --extra mcp`, or `pip install "tts-engine[mcp]"`) plus the extra for your provider. Without it, `tts-engine-mcp` exits with `tts-engine-mcp requires the mcp extra: pip install tts-engine[mcp]`.

Its configuration wraps the same engine block as a library config ([configuration.md](configuration.md)) with an optional `server` block:

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

Start from the example for your module in [`examples/`](../examples), then run:

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
