import pytest

# The variable each hosted module reads its key from by default (`api_key_env`).
PROVIDER_KEYS = (
    "CARTESIA_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "GEMINI_API_KEY",
    "GRADIUM_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every provider's default key variable set to "k": a config never holds a key, so
    the tests give one the way a user does, and the shell's real keys never reach them."""
    for name in PROVIDER_KEYS:
        monkeypatch.setenv(name, "k")
