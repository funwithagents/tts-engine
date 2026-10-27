"""Unit tests for KokoroModule with kokoro-onnx and huggingface_hub faked.

The fast tier must never download or load a model, so `kokoro_onnx`,
`huggingface_hub` and `huggingface_hub.errors` are faked via `sys.modules`.
Config-validation tests need no fake — validation runs before the lazy import.
"""

import asyncio
import gc
import sys
import time
import types
import weakref

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions, float_to_int16_pcm
from tts_engine.modules.kokoro import KokoroModule

_REVISION = "8d07950c9b6c87ce6809e9bba7bd494336217c2a"


class RemoteEntryNotFoundError(Exception):
    pass


class RepositoryNotFoundError(Exception):
    pass


class RevisionNotFoundError(Exception):
    pass


class LocalEntryNotFoundError(FileNotFoundError):
    """Offline and not cached: a FileNotFoundError, as in huggingface_hub."""


class _Fakes:
    """Shared state for one test: what the fakes recorded, and how they behave."""

    def __init__(self) -> None:
        self.downloads: list[tuple[str, str, str | None]] = []
        self.download_error: Exception | None = None
        self.init_error: Exception | None = None
        self.loaded: list[tuple[str, str]] = []
        self.voices = ["af_heart", "bf_emma", "ff_siwis", "xx_custom"]
        self.bad_languages: set[str] = set()
        self.creates: list[tuple[str, dict]] = []
        self.create_error: Exception | None = None
        self.audio = np.array([0.0, 0.5, -0.5, 1.0, 1.5, -2.0], dtype=np.float32)
        self.rate = 24000
        self.instances: list = []


class _FakeTokenizer:
    def __init__(self, fakes: _Fakes) -> None:
        self._fakes = fakes

    def phonemize(self, text, lang="en-us"):
        if lang in self._fakes.bad_languages:
            raise RuntimeError(f'language "{lang}" is not supported')
        return text


def _install_fakes(monkeypatch, fakes: _Fakes) -> None:
    def hf_hub_download(repo_id, filename, *, revision=None):
        fakes.downloads.append((repo_id, filename, revision))
        if fakes.download_error is not None:
            raise fakes.download_error
        return f"/cache/{repo_id}/{filename}"

    class FakeKokoro:
        def __init__(self, model_path, voices_path) -> None:
            fakes.loaded.append((model_path, voices_path))
            if fakes.init_error is not None:
                raise fakes.init_error
            fakes.instances.append(self)
            self.tokenizer = _FakeTokenizer(fakes)
            self.sess = types.SimpleNamespace(
                get_providers=lambda: ["CPUExecutionProvider"]
            )

        def get_voices(self):
            return sorted(fakes.voices)

        def create(self, text, **kwargs):
            fakes.creates.append((text, kwargs))
            if fakes.create_error is not None:
                raise fakes.create_error
            return fakes.audio, fakes.rate

    errors_mod = types.SimpleNamespace(
        RemoteEntryNotFoundError=RemoteEntryNotFoundError,
        RepositoryNotFoundError=RepositoryNotFoundError,
        RevisionNotFoundError=RevisionNotFoundError,
    )
    hub_mod = types.SimpleNamespace(hf_hub_download=hf_hub_download, errors=errors_mod)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_mod)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors_mod)
    monkeypatch.setitem(
        sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro)
    )


@pytest.fixture
def fakes(monkeypatch) -> _Fakes:
    state = _Fakes()
    _install_fakes(monkeypatch, state)
    return state


# --- config validation (before any download) ---------------------------------


@pytest.mark.parametrize(
    "config, match",
    [
        ({"voice": ""}, "voice"),
        ({"language": ""}, "language"),
        ({"model": ""}, "model"),
        ({"voices": ""}, "voices"),
        ({"speed": 0.4}, "speed"),
        ({"speed": 2.5}, "speed"),
        ({"model": "hf://owner/repo"}, "model"),
        ({"model": "hf://owner//file.onnx"}, "model"),
        ({"model": "hf://o/r/f.onnx@"}, "model"),
        ({"voice": "xx_custom"}, "no default language"),
    ],
)
def test_invalid_config_raises_before_any_download(fakes, config, match):
    with pytest.raises(ConfigError, match=match):
        KokoroModule({"type": "kokoro", **config})
    assert fakes.downloads == []


def test_missing_local_file_raises_before_load(fakes, tmp_path):
    with pytest.raises(ConfigError, match="voices"):
        KokoroModule({"type": "kokoro", "voices": "v.bin", "base_dir": str(tmp_path)})
    assert fakes.loaded == []


# --- model files -------------------------------------------------------------


def test_defaults_download_the_pinned_files(fakes):
    module = KokoroModule({"type": "kokoro"})
    assert fakes.downloads == [
        ("fastrtc/kokoro-onnx", "kokoro-v1.0.onnx", _REVISION),
        ("fastrtc/kokoro-onnx", "voices-v1.0.bin", _REVISION),
    ]
    assert fakes.loaded == [
        (
            "/cache/fastrtc/kokoro-onnx/kokoro-v1.0.onnx",
            "/cache/fastrtc/kokoro-onnx/voices-v1.0.bin",
        )
    ]
    assert module.sample_rate == 24000


def test_hf_reference_is_split_into_repo_file_and_revision(fakes):
    KokoroModule(
        {
            "type": "kokoro",
            "model": "hf://me/repo/onnx/model.onnx@v2",
            "voices": "hf://me/repo/voices.bin",
        }
    )
    assert fakes.downloads == [
        ("me/repo", "onnx/model.onnx", "v2"),
        ("me/repo", "voices.bin", None),
    ]


def test_local_files_resolve_against_base_dir(fakes, tmp_path):
    (tmp_path / "m.onnx").touch()
    (tmp_path / "v.bin").touch()
    KokoroModule(
        {
            "type": "kokoro",
            "model": "m.onnx",
            "voices": "v.bin",
            "base_dir": str(tmp_path),
        }
    )
    assert fakes.downloads == []
    assert fakes.loaded == [(str(tmp_path / "m.onnx"), str(tmp_path / "v.bin"))]


@pytest.mark.parametrize(
    "exc",
    [
        RemoteEntryNotFoundError("no file"),
        RepositoryNotFoundError("no repo"),
        RevisionNotFoundError("no rev"),
    ],
)
def test_wrong_hub_reference_is_a_config_error(fakes, exc):
    fakes.download_error = exc
    with pytest.raises(ConfigError, match=str(exc)):
        KokoroModule({"type": "kokoro"})


@pytest.mark.parametrize(
    "exc", [LocalEntryNotFoundError("offline"), ConnectionError("hub down")]
)
def test_other_download_failure_is_a_tts_error(fakes, exc):
    fakes.download_error = exc
    with pytest.raises(TTSError, match=str(exc)):
        KokoroModule({"type": "kokoro"})


def test_load_failure_is_a_tts_error(fakes):
    fakes.init_error = RuntimeError("corrupt onnx")
    with pytest.raises(TTSError, match="corrupt onnx"):
        KokoroModule({"type": "kokoro"})


# --- voice and language ------------------------------------------------------


def test_unknown_voice_after_load_raises(fakes):
    with pytest.raises(ConfigError, match="voice"):
        KokoroModule({"type": "kokoro", "voice": "af_nobody"})


def test_language_the_phonemizer_rejects_raises(fakes):
    fakes.bad_languages = {"xx-yy"}
    with pytest.raises(ConfigError, match="xx-yy"):
        KokoroModule({"type": "kokoro", "language": "xx-yy"})


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, {"voice": "af_heart", "lang": "en-us"}),
        ({"voice": "ff_siwis"}, {"voice": "ff_siwis", "lang": "fr-fr"}),
        (
            {"voice": "af_heart", "language": "en-gb"},
            {"voice": "af_heart", "lang": "en-gb"},
        ),
        (
            {"voice": "xx_custom", "language": "de"},
            {"voice": "xx_custom", "lang": "de"},
        ),
        ({"speed": 1.2}, {"voice": "af_heart", "lang": "en-us", "speed": 1.2}),
    ],
)
def test_create_gets_the_voice_language_and_only_set_options(fakes, config, expected):
    module = KokoroModule({"type": "kokoro", **config})
    asyncio.run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))
    assert fakes.creates == [("Hi.", expected)]


# --- synthesize_text ---------------------------------------------------------


def test_one_create_per_sentence_and_int16_pcm(fakes):
    module = KokoroModule({"type": "kokoro"})
    received = bytearray()
    asyncio.run(
        module.synthesize_text(
            "Hello world. How are you?", TTSOptions(), received.extend
        )
    )
    assert [text for text, _ in fakes.creates] == ["Hello world.", "How are you?"]
    # Each sentence's float audio, converted and fed (the conversion's values
    # are float_to_int16_pcm's own test).
    assert bytes(received) == float_to_int16_pcm(fakes.audio) * 2


def test_a_sentence_is_fed_in_100ms_slices(fakes):
    fakes.audio = np.zeros(5000, dtype=np.float32)
    module = KokoroModule({"type": "kokoro"})
    sizes: list[int] = []
    asyncio.run(
        module.synthesize_text("Hi.", TTSOptions(), lambda b: sizes.append(len(b)))
    )
    assert sizes == [4800, 4800, 400]  # 2400 + 2400 + 200 samples


def test_unexpected_rate_is_a_tts_error(fakes):
    fakes.rate = 22050
    module = KokoroModule({"type": "kokoro"})
    with pytest.raises(TTSError, match="22050"):
        asyncio.run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))


def test_create_error_is_a_tts_error(fakes):
    fakes.create_error = RuntimeError("inference boom")
    module = KokoroModule({"type": "kokoro"})
    with pytest.raises(TTSError, match="inference boom"):
        asyncio.run(module.synthesize_text("Hi.", TTSOptions(), lambda _b: None))


# --- cancellation ------------------------------------------------------------


def _slow_counting_callback(loop: asyncio.AbstractEventLoop, first: asyncio.Event):
    """A callback that plays each slice for a millisecond and signals the loop
    on the first one; `count()` reads how many slices it has received."""
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first.set)
        time.sleep(0.001)

    return callback, lambda: count


async def test_cancel_stops_callbacks_and_further_inference(fakes):
    fakes.audio = np.zeros(2400 * 1000, dtype=np.float32)  # 1000 slices per sentence
    module = KokoroModule({"type": "kokoro"})
    first = asyncio.Event()
    callback, count = _slow_counting_callback(asyncio.get_running_loop(), first)

    task = asyncio.create_task(
        module.synthesize_text("One. Two. Three. Four.", TTSOptions(), callback)
    )
    await first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert count() < 1000  # `stop` reached the slice feeder mid-sentence


async def test_text_stream_cancel_mid_audio_stops_callbacks(fakes):
    fakes.audio = np.zeros(2400 * 1000, dtype=np.float32)
    module = KokoroModule({"type": "kokoro"})
    first = asyncio.Event()
    callback, count = _slow_counting_callback(asyncio.get_running_loop(), first)

    async def source():
        yield "Hello world. "  # a complete sentence: inference starts
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert count() < 1000
    assert [text for text, _ in fakes.creates] == ["Hello world."]


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_creates_once_per_sentence_before_the_source_finishes(
    fakes,
):
    # Sentences split across pieces, and a source that only goes on once the
    # first sentence's audio is fed: a module that waited for the whole text
    # would deadlock here.
    module = KokoroModule({"type": "kokoro"})
    loop = asyncio.get_running_loop()
    got_audio = asyncio.Event()
    received = bytearray()

    def callback(chunk: bytes) -> None:
        received.extend(chunk)
        loop.call_soon_threadsafe(got_audio.set)

    async def source():
        yield "Hello wor"
        yield "ld. How are"
        await got_audio.wait()
        yield " you? Fine"

    await asyncio.wait_for(
        module.synthesize_text_stream(source(), TTSOptions(), callback), timeout=5
    )
    assert [text for text, _ in fakes.creates] == [
        "Hello world.",
        "How are you?",
        "Fine",
    ]
    assert len(received) == 3 * 2 * 6  # six int16 samples per fake sentence


def test_close_drops_the_runtime(fakes):
    module = KokoroModule({"type": "kokoro"})
    runtime = weakref.ref(fakes.instances.pop())
    gc.collect()
    assert runtime() is not None  # the module holds it until closed

    module.close()
    gc.collect()
    assert runtime() is None
