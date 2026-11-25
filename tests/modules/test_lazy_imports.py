"""Guard: the library imports cleanly with no provider extra installed.

Runs in a subprocess with every provider library made unimportable, so
poisoning `sys.modules` never leaks into the rest of the suite. Proves that
`import tts_engine` and the registry load, that constructing each provider
raises the `pip install tts-engine[<name>]` hint, and that the fixture modules
still construct.
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import sys

    # Make any import of a provider library raise ImportError — as in a base
    # install with no extras.
    for name in (
        "elevenlabs",
        "elevenlabs.types",
        "miniaudio",
        "pocket_tts",
        "torch",
        "gradium",
        "aiohttp",
        "ptts",
        "mistralai",
        "deepgram",
        "google.genai",
        "cartesia",
        "openai",
        "qwentts_cpp",
        "kokoro_onnx",
        "piper",
    ):
        sys.modules[name] = None

    import tts_engine  # must not import any provider library
    from tts_engine.config import ConfigError
    from tts_engine.modules import REGISTRY

    assert set(REGISTRY) == {
        "elevenlabs",
        "pocket",
        "gradium",
        "phonon",
        "mistral",
        "deepgram",
        "gemini",
        "cartesia",
        "openai",
        "openai-speech-compatible",
        "qwen3",
        "kokoro",
        "piper",
        "tone",
        "audiofile",
    }, REGISTRY

    for module_type, config in (
        ("elevenlabs", {"type": "elevenlabs", "voice_id": "v"}),
        ("pocket", {"type": "pocket"}),
        ("gradium", {"type": "gradium", "voice_id": "v"}),
        ("phonon", {"type": "phonon"}),
        ("mistral", {"type": "mistral", "voice_id": "v"}),
        ("deepgram", {"type": "deepgram"}),
        ("gemini", {"type": "gemini"}),
        ("cartesia", {"type": "cartesia", "voice_id": "v"}),
        ("openai", {"type": "openai"}),
        (
            "openai-speech-compatible",
            {"type": "openai-speech-compatible", "voice": "v"},
        ),
        ("qwen3", {"type": "qwen3"}),
        ("kokoro", {"type": "kokoro"}),
        ("piper", {"type": "piper"}),
    ):
        try:
            REGISTRY[module_type](config)
        except ConfigError as exc:
            # A protocol module shares its origin's `<origin>-compatible` extra.
            extra = {"openai-speech-compatible": "openai-compatible"}.get(
                module_type, module_type
            )
            assert f"tts-engine[{extra}]" in str(exc), exc
        else:
            raise AssertionError(f"{module_type} constructed without its extra")

    REGISTRY["tone"]({"type": "tone"})
    print("OK")
    """
)


def test_package_imports_without_provider_extras():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
