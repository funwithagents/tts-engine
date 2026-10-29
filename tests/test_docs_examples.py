"""The documentation's runnable examples, executed as written (specs/project.md,
"Documentation checks").

Each Python block a user is likely to copy is extracted from its Markdown file
and run with the `tone` fixture standing in for the provider it names and a
recording player standing in for the sound device, so the imports, names and
calls it shows cannot drift from the library.
"""

import ast
import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

import tts_engine.engine
from tts_engine import VoiceInfo
from tts_engine.modules import REGISTRY
from tts_engine.modules.tone import ToneModule

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")


def _python_block(path: Path, heading: str) -> str:
    """The first fenced `python` block of `path` under the Markdown heading
    `heading` (headings inside code blocks are not headings)."""
    in_section = in_fence = in_python = False
    level = 0
    block: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if in_python:
                return "\n".join(block)
            in_fence = not in_fence
            in_python = in_fence and in_section and line.strip() == "```python"
            continue
        if in_python:
            block.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            if match.group(2) == heading:
                in_section, level = True, len(match.group(1))
            elif in_section and len(match.group(1)) <= level:
                break
    raise AssertionError(f"no python block under {heading!r} in {path.name}")


def _run(source: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Execute a block, awaiting it when it uses top-level `await` as the
    docs' snippets do (they run inside the reader's async code)."""
    code = compile(source, "<docs>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    if code.co_flags & inspect.CO_COROUTINE:
        asyncio.run(eval(code, namespace))
    else:
        exec(code, namespace)  # noqa: S102  (the docs' own examples, run as written)
    return namespace


class _RecordingPlayer:
    """Stands in for `AudioPlayer`: same constructor, records what it is fed."""

    fed: ClassVar[list[bytes]] = []

    def __init__(self, device: object = None, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate

    def feed(self, chunk: bytes) -> None:
        _RecordingPlayer.fed.append(chunk)

    def drain(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The examples name pocket (and cartesia for voices): tone plays for both,
    # reading only its own fields, and nothing reaches a sound device.
    monkeypatch.setitem(REGISTRY, "pocket", ToneModule)
    monkeypatch.setitem(REGISTRY, "cartesia", ToneModule)
    monkeypatch.setattr(tts_engine.engine, "AudioPlayer", _RecordingPlayer)
    _RecordingPlayer.fed = []
    # The files the examples load, with the README's module block.
    engine_block = {"module": {"type": "pocket", "voice": "alba"}}
    (tmp_path / "tts.json").write_text(json.dumps(engine_block), encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"engine": engine_block}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)


def _config() -> Any:
    from tts_engine import TTSEngineConfig

    return TTSEngineConfig(module={"type": "pocket", "voice": "alba"})


def test_readme_quick_start():
    _run(_python_block(_REPO_ROOT / "README.md", "Quick start"), {})
    assert _RecordingPlayer.fed


def test_readme_engine_as_context_manager():
    from tts_engine import TTSEngine

    source = _python_block(_REPO_ROOT / "README.md", "How the engine works")
    namespace = _run(source, {"TTSEngine": TTSEngine, "config": _config()})
    assert _RecordingPlayer.fed
    with pytest.raises(RuntimeError):
        asyncio.run(namespace["engine"].say("closed"))


def test_configuration_patterns():
    source = _python_block(
        _REPO_ROOT / "docs" / "configuration.md", "Configuring an engine"
    )
    application_config = {"tts": {"module": {"type": "pocket", "voice": "alba"}}}
    namespace = _run(source, {"application_config": application_config})
    assert namespace["engine"].sample_rate == 24000


def test_custom_sink():
    from tts_engine import TTSEngine

    source = _python_block(
        _REPO_ROOT / "docs" / "audio-sinks.md", "Audio output and custom sinks"
    )
    namespace = _run(source, {"TTSEngine": TTSEngine, "config": _config()})
    assert namespace["sink"].chunks
    assert not _RecordingPlayer.fed


def test_streaming_an_llm_response():
    class _Stream:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc: object) -> None:
            pass

        @property
        async def text_stream(self):
            for token in ["Hello ", "from ", "a ", "stream."]:
                yield token

    class _Client:
        class messages:
            @staticmethod
            def stream(*args: object, **kwargs: object) -> _Stream:
                return _Stream()

    from tts_engine import TTSEngine

    source = _python_block(
        _REPO_ROOT / "docs" / "streaming.md", "Speak an LLM response as it streams"
    )
    _run(source, {"engine": TTSEngine(_config()), "llm_client": _Client()})
    assert _RecordingPlayer.fed


def test_tools_in_an_agent():
    source = _python_block(
        _REPO_ROOT / "docs" / "agents-and-mcp.md", "Use `TTSTools` in an agent"
    )
    namespace = _run(source, {})
    assert namespace["result"] == "OK"


def test_engine_from_an_mcp_server_config():
    source = _python_block(
        _REPO_ROOT / "docs" / "agents-and-mcp.md", "Run the MCP server"
    )
    namespace = _run(source, {})
    assert namespace["engine"].sample_rate == 24000


def test_calibrating_the_gain():
    source = _python_block(
        _REPO_ROOT / "docs" / "configuration.md", "Finding the value"
    )
    namespace = _run(source, {})
    # tone stands in for pocket: a sine at 0.2 of full scale.
    assert namespace["result"].level.level_dbfs == -17.0
    assert namespace["result"].suggestion.gain_db == -5.0
    assert not _RecordingPlayer.fed  # measured, not played


def test_finding_a_voice(monkeypatch: pytest.MonkeyPatch):
    voice = VoiceInfo(
        id="v1",
        name="Zeke",
        config={"voice_id": "v1"},
        languages=("en",),
        gender="male",
    )
    monkeypatch.setattr(
        ToneModule, "list_voices", classmethod(lambda cls, config: [voice])
    )
    namespace = _run(
        _python_block(_REPO_ROOT / "docs" / "voices.md", "Finding a voice"), {}
    )
    assert namespace["config"] == {
        "type": "cartesia",
        "api_key_env": "CARTESIA_API_KEY",
        "voice_id": "v1",
    }
