"""Guard: the engine and tools work without the mcp extra installed.

Runs in a subprocess with `mcp` and `uvicorn` made unimportable, so poisoning
`sys.modules` never leaks into the rest of the suite. Proves that
`import tts_engine`, `MCPServerConfig`, and `TTSTools(...).say` on the `tone`
fixture module never import the MCP stack, and that `tts-engine-mcp` exits
with the `pip install tts-engine[mcp]` hint.
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    # As in a base install without the mcp extra.
    for name in ("mcp", "uvicorn"):
        sys.modules[name] = None

    from tts_engine import MCPServerConfig, TTSEngine, TTSTools

    cfg = MCPServerConfig.from_dict({"engine": {"module": {"type": "tone"}}})


    class CaptureSink:
        def __init__(self):
            self.chunks = []

        def feed(self, chunk):
            self.chunks.append(chunk)

        def drain(self):
            pass


    sink = CaptureSink()
    tools = TTSTools(TTSEngine(cfg.engine, sink=sink))
    assert asyncio.run(tools.say("hi")) == "OK"
    assert sink.chunks

    from tts_engine.mcp_server_cli import main

    # The config file does not exist: the extra check must run before it is read.
    sys.argv = ["tts-engine-mcp", "--config", "unused.json"]
    try:
        main()
    except SystemExit as exc:
        assert "tts-engine[mcp]" in str(exc.code), exc.code
    else:
        raise AssertionError("main() ran without the mcp extra")

    # Never imported: still the poison sentinels we planted.
    assert sys.modules["mcp"] is None and sys.modules["uvicorn"] is None
    print("OK")
    """
)


def test_engine_and_tools_work_without_mcp_extra():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
