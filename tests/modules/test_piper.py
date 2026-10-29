"""Unit tests for PiperModule with piper-tts and huggingface_hub faked.

The fast tier must never download or load a voice, so `piper`,
`huggingface_hub` and `huggingface_hub.errors` are faked via `sys.modules`.
Config-validation tests need no fake — validation runs before the lazy import.
"""

import asyncio
import enum
import gc
import json
import sys
import time
import types
import weakref
from dataclasses import dataclass

import numpy as np
import pytest

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.piper import PiperModule
from tts_engine.modules.piper import module as piper_module

_REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"


class RemoteEntryNotFoundError(Exception):
    pass


class RepositoryNotFoundError(Exception):
    pass


class RevisionNotFoundError(Exception):
    pass


class LocalEntryNotFoundError(FileNotFoundError):
    """Offline and not cached: a FileNotFoundError, as in huggingface_hub."""


class PhonemeType(enum.Enum):
    ESPEAK = "espeak"
    PINYIN = "pinyin"
    JAPANESE = "japanese"


@dataclass
class SynthesisConfig:
    speaker_id: int | None = None
    length_scale: float | None = None


@dataclass
class _Chunk:
    sample_rate: int
    audio_int16_bytes: bytes


class _FakeSessionOptions:
    def __init__(self) -> None:
        self.intra_op_num_threads = 0


class _FakeInferenceSession:
    """Records how the module built the session when `threads` is set."""

    def __init__(self, model_path, sess_options=None, providers=None) -> None:
        self._model_path = model_path
        self.sess_options = sess_options
        self.providers = providers

    def get_providers(self):
        return list(self.providers or [])


class _Fakes:
    """Shared state for one test: what the fakes recorded, and how they behave."""

    def __init__(self) -> None:
        self.downloads: list[tuple[str, str, str | None]] = []
        self.download_error: Exception | None = None
        self.load_error: Exception | None = None
        self.loaded: list[tuple[str, str | None]] = []
        self.sample_rate = 22050
        self.num_speakers = 1
        self.speaker_id_map: dict[str, int] = {}
        self.instances: list = []
        self.length_scale = 1.2
        self.phoneme_type = PhonemeType.ESPEAK
        self.phonemize_error: Exception | None = None
        self.synth_calls: list[tuple[str, SynthesisConfig | None]] = []
        self.synth_error: Exception | None = None
        self.chunk_rate: int | None = None  # defaults to the voice's rate
        self.pcm = np.array([1, -2, 3], dtype="<i2").tobytes()
        self.sessions: list[_FakeInferenceSession] = []
        self.config_dicts: list[dict] = []

    def config(self):
        return types.SimpleNamespace(
            sample_rate=self.sample_rate,
            num_speakers=self.num_speakers,
            speaker_id_map=self.speaker_id_map,
            length_scale=self.length_scale,
            phoneme_type=self.phoneme_type,
        )


def _install_fakes(monkeypatch, fakes: _Fakes) -> None:
    def hf_hub_download(repo_id, filename, *, revision=None):
        fakes.downloads.append((repo_id, filename, revision))
        if fakes.download_error is not None:
            raise fakes.download_error
        return f"/cache/{repo_id}/{filename}"

    class FakeVoice:
        def __init__(self, config=None, session=None) -> None:
            self.config = config or fakes.config()
            self.session = session
            if session is not None:  # built by the module, not by load()
                fakes.sessions.append(session)
            fakes.instances.append(self)

        @staticmethod
        def load(model_path, config_path=None):
            fakes.loaded.append((model_path, config_path))
            if fakes.load_error is not None:
                raise fakes.load_error
            return FakeVoice()

        def phonemize(self, text):
            if fakes.phonemize_error is not None:
                raise fakes.phonemize_error
            return [list(text)]

        def synthesize(self, text, syn_config=None):
            fakes.synth_calls.append((text, syn_config))
            if fakes.synth_error is not None:
                raise fakes.synth_error
            yield _Chunk(fakes.chunk_rate or fakes.sample_rate, fakes.pcm)

    errors_mod = types.SimpleNamespace(
        RemoteEntryNotFoundError=RemoteEntryNotFoundError,
        RepositoryNotFoundError=RepositoryNotFoundError,
        RevisionNotFoundError=RevisionNotFoundError,
    )
    hub_mod = types.SimpleNamespace(hf_hub_download=hf_hub_download, errors=errors_mod)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_mod)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors_mod)

    def from_dict(config_dict: dict):
        fakes.config_dicts.append(config_dict)
        return fakes.config()

    piper_mod = types.SimpleNamespace(
        PiperVoice=FakeVoice,
        SynthesisConfig=SynthesisConfig,
        PiperConfig=types.SimpleNamespace(from_dict=from_dict),
    )
    monkeypatch.setitem(sys.modules, "piper", piper_mod)
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        types.SimpleNamespace(
            SessionOptions=_FakeSessionOptions, InferenceSession=_FakeInferenceSession
        ),
    )


@pytest.fixture
def fakes(monkeypatch) -> _Fakes:
    state = _Fakes()
    _install_fakes(monkeypatch, state)
    return state


def _say(module: PiperModule, text: str, callback=lambda _b: None) -> None:
    asyncio.run(module.synthesize_text(text, TTSOptions(), callback))


# --- config validation (before any download) ---------------------------------


@pytest.mark.parametrize(
    "config, match",
    [
        ({"voice": ""}, "voice"),
        ({"model": ""}, "model"),
        ({"voice": "en_US-ljspeech-medium", "model": "m.onnx"}, "not both"),
        ({"voice": "ljspeech"}, "voice"),
        ({"voice": "en_US-ljspeech"}, "voice"),
        ({"voice": "en_US--medium"}, "voice"),
        ({"voice": "a-b-c-d"}, "voice"),
        ({"model": "hf://owner/repo"}, "model"),
        ({"model": "hf://o//v.onnx"}, "model"),
        ({"model": "hf://o/r/v.onnx@"}, "model"),
        ({"speaker": -1}, "speaker"),
        ({"speaker": True}, "speaker"),
        ({"speaker": ""}, "speaker"),
        ({"speaker": 1.5}, "speaker"),
        ({"speed": 0.4}, "speed"),
        ({"speed": 2.5}, "speed"),
        ({"threads": 0}, "threads"),
        ({"threads": True}, "threads"),
        ({"threads": "2"}, "threads"),
    ],
)
def test_invalid_config_raises_before_any_download(fakes, config, match):
    with pytest.raises(ConfigError, match=match):
        PiperModule({"type": "piper", **config})
    assert fakes.downloads == []


# --- voice files -------------------------------------------------------------


def test_default_voice_downloads_the_pinned_pair(fakes):
    fakes.sample_rate = 16000  # not the default voice's 22050: read from the voice
    module = PiperModule({"type": "piper"})
    path = "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx"
    assert fakes.downloads == [
        ("rhasspy/piper-voices", path, _REVISION),
        ("rhasspy/piper-voices", f"{path}.json", _REVISION),
    ]
    assert fakes.loaded == [
        (
            f"/cache/rhasspy/piper-voices/{path}",
            f"/cache/rhasspy/piper-voices/{path}.json",
        )
    ]
    assert module.sample_rate == 16000


def test_voice_key_maps_to_its_catalog_path(fakes):
    PiperModule({"type": "piper", "voice": "pt_BR-faber-medium"})
    assert fakes.downloads[0] == (
        "rhasspy/piper-voices",
        "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx",
        _REVISION,
    )


def test_hf_model_fetches_the_onnx_and_its_json(fakes):
    PiperModule({"type": "piper", "model": "hf://me/voices/x/v.onnx@v1"})
    assert fakes.downloads == [
        ("me/voices", "x/v.onnx", "v1"),
        ("me/voices", "x/v.onnx.json", "v1"),
    ]


def test_local_model_resolves_against_base_dir(fakes, tmp_path):
    (tmp_path / "v.onnx").touch()
    (tmp_path / "v.onnx.json").touch()
    PiperModule({"type": "piper", "model": "v.onnx", "base_dir": str(tmp_path)})
    assert fakes.downloads == []
    assert fakes.loaded == [(str(tmp_path / "v.onnx"), f"{tmp_path / 'v.onnx'}.json")]


def test_threads_build_the_voice_on_a_session_with_the_thread_count(fakes, tmp_path):
    (tmp_path / "v.onnx").touch()
    (tmp_path / "v.onnx.json").write_text('{"audio": {"sample_rate": 22050}}')
    fakes.sample_rate = 16000  # the config the fake PiperConfig builds
    module = PiperModule(
        {"type": "piper", "model": "v.onnx", "base_dir": str(tmp_path), "threads": 2}
    )
    assert fakes.loaded == []  # not PiperVoice.load: the module built the voice
    (session,) = fakes.sessions
    assert session._model_path == str(tmp_path / "v.onnx")
    assert session.sess_options.intra_op_num_threads == 2
    assert session.providers == ["CPUExecutionProvider"]
    assert fakes.config_dicts == [{"audio": {"sample_rate": 22050}}]  # the JSON
    assert module.sample_rate == 16000
    _say(module, "Hi.")
    assert [t for t, _ in fakes.synth_calls] == ["Hi."]


@pytest.mark.parametrize("present", ["v.onnx", "v.onnx.json"])
def test_missing_local_file_raises_before_load(fakes, tmp_path, present):
    (tmp_path / present).touch()
    with pytest.raises(ConfigError, match="not found"):
        PiperModule({"type": "piper", "model": "v.onnx", "base_dir": str(tmp_path)})
    assert fakes.loaded == []


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
        PiperModule({"type": "piper"})


@pytest.mark.parametrize(
    "exc", [LocalEntryNotFoundError("offline"), ConnectionError("hub down")]
)
def test_other_download_failure_is_a_tts_error(fakes, exc):
    fakes.download_error = exc
    with pytest.raises(TTSError, match=str(exc)):
        PiperModule({"type": "piper"})


def test_load_failure_is_a_tts_error(fakes):
    fakes.load_error = RuntimeError("corrupt onnx")
    with pytest.raises(TTSError, match="corrupt onnx"):
        PiperModule({"type": "piper"})


# --- phoneme types -----------------------------------------------------------


def test_pinyin_voice_is_rejected(fakes):
    fakes.phoneme_type = PhonemeType.PINYIN
    with pytest.raises(ConfigError, match="pinyin"):
        PiperModule({"type": "piper", "voice": "zh_CN-chaowen-medium"})


def test_phonemizer_missing_its_package_is_a_config_error(fakes):
    fakes.phoneme_type = PhonemeType.JAPANESE
    fakes.phonemize_error = ModuleNotFoundError("No module named 'pyopenjtalk'")
    with pytest.raises(ConfigError, match="pyopenjtalk"):
        PiperModule({"type": "piper", "voice": "ja_JP-hi_fi_captain-medium"})


# --- speaker and speed -------------------------------------------------------


@pytest.mark.parametrize(("speaker", "expected"), [("slt", 2), (3, 3)])
def test_speaker_by_name_or_index(fakes, speaker, expected):
    fakes.num_speakers = 18
    fakes.speaker_id_map = {"awb": 0, "rms": 1, "slt": 2}
    module = PiperModule({"type": "piper", "speaker": speaker})
    _say(module, "Hi.")
    assert fakes.synth_calls[0][1] == SynthesisConfig(speaker_id=expected)


@pytest.mark.parametrize("speaker", ["zzz", 18])
def test_unknown_speaker_after_load_raises(fakes, speaker):
    fakes.num_speakers = 18
    fakes.speaker_id_map = {"awb": 0}
    with pytest.raises(ConfigError, match="speaker"):
        PiperModule({"type": "piper", "speaker": speaker})


def test_speaker_on_a_single_speaker_voice_raises(fakes):
    with pytest.raises(ConfigError, match="one speaker"):
        PiperModule({"type": "piper", "speaker": 0})


def test_only_set_options_reach_synthesis(fakes):
    _say(PiperModule({"type": "piper"}), "Hi.")
    _say(PiperModule({"type": "piper", "speed": 1.5}), "Hi.")
    assert fakes.synth_calls[0][1] == SynthesisConfig()
    # length_scale = the voice's own (1.2) / speed
    speed_config = fakes.synth_calls[1][1]
    assert speed_config is not None and speed_config.speaker_id is None
    assert speed_config.length_scale == pytest.approx(0.8)


# --- synthesize_text ---------------------------------------------------------


def test_one_call_per_sentence_and_pcm_unchanged(fakes):
    received = bytearray()
    _say(PiperModule({"type": "piper"}), "Hello world. How are you?", received.extend)
    assert [text for text, _ in fakes.synth_calls] == ["Hello world.", "How are you?"]
    assert bytes(received) == fakes.pcm * 2


def test_a_sentence_is_fed_in_100ms_slices(fakes):
    fakes.pcm = bytes(2 * 5000)  # 5000 samples at 22050 Hz
    sizes: list[int] = []
    _say(PiperModule({"type": "piper"}), "Hi.", lambda b: sizes.append(len(b)))
    assert sizes == [4410, 4410, 1180]  # 2205 + 2205 + 590 samples


def test_unexpected_chunk_rate_is_a_tts_error(fakes):
    fakes.chunk_rate = 16000
    with pytest.raises(TTSError, match="16000"):
        _say(PiperModule({"type": "piper"}), "Hi.")


def test_synthesis_error_is_a_tts_error(fakes):
    fakes.synth_error = RuntimeError("inference boom")
    with pytest.raises(TTSError, match="inference boom"):
        _say(PiperModule({"type": "piper"}), "Hi.")


# --- cancellation ------------------------------------------------------------


def _slow_counting_callback(loop: asyncio.AbstractEventLoop, first: asyncio.Event):
    """Plays each slice for a millisecond and signals the loop on the first."""
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first.set)
        time.sleep(0.001)

    return callback, lambda: count


async def test_cancel_stops_callbacks_and_further_synthesis(fakes):
    fakes.pcm = bytes(4410 * 1000)  # 1000 slices per sentence
    module = PiperModule({"type": "piper"})
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
    fakes.pcm = bytes(4410 * 1000)
    module = PiperModule({"type": "piper"})
    first = asyncio.Event()
    callback, count = _slow_counting_callback(asyncio.get_running_loop(), first)

    async def source():
        yield "Hello world. "  # a complete sentence: synthesis starts
        await asyncio.Event().wait()  # then the source stays open

    task = asyncio.create_task(
        module.synthesize_text_stream(source(), TTSOptions(), callback)
    )
    await first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert count() < 1000
    assert [text for text, _ in fakes.synth_calls] == ["Hello world."]


# --- synthesize_text_stream --------------------------------------------------


async def test_text_stream_synthesizes_once_per_sentence_before_the_source_finishes(
    fakes,
):
    # Sentences split across pieces, and a source that only goes on once the
    # first sentence's audio is fed: a module that waited for the whole text
    # would deadlock here.
    module = PiperModule({"type": "piper"})
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
    assert [text for text, _ in fakes.synth_calls] == [
        "Hello world.",
        "How are you?",
        "Fine",
    ]
    assert bytes(received) == fakes.pcm * 3


def test_close_drops_the_voice(fakes):
    module = PiperModule({"type": "piper"})
    voice = weakref.ref(fakes.instances.pop())
    gc.collect()
    assert voice() is not None  # the module holds it until closed

    module.close()
    gc.collect()
    assert voice() is None


# --- voice listing -----------------------------------------------------------


def test_list_voices_has_one_entry_per_voice_or_speaker(monkeypatch, tmp_path):
    catalog = {
        "en_US-ryan-medium": {
            "language": {
                "code": "en_US",
                "name_english": "English",
                "country_english": "United States",
            },
            "quality": "medium",
            "num_speakers": 1,
            "speaker_id_map": {},
        },
        "en_US-arctic-medium": {
            "language": {
                "code": "en_US",
                "name_english": "English",
                "country_english": "United States",
            },
            "quality": "medium",
            "num_speakers": 2,
            "speaker_id_map": {"rms": 1, "bdl": 0},
        },
    }
    path = tmp_path / "voices.json"
    path.write_text(json.dumps(catalog))
    downloads: list[tuple] = []

    def hf_hub_download(repo_id, filename, revision=None):
        downloads.append((repo_id, filename, revision))
        return str(path)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = hf_hub_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    ryan, bdl, rms = PiperModule.list_voices({"type": "piper"})

    assert downloads == [
        ("rhasspy/piper-voices", "voices.json", piper_module._VOICES_REVISION)
    ]
    assert ryan.config == {"voice": "en_US-ryan-medium"}
    assert (ryan.languages, ryan.description, ryan.tags) == (
        ("en_US",),
        "English, United States",
        ("medium",),
    )
    # speakers in id order, each selected with `speaker`
    assert (bdl.id, bdl.config) == (
        "en_US-arctic-medium/bdl",
        {"voice": "en_US-arctic-medium", "speaker": "bdl"},
    )
    assert rms.config == {"voice": "en_US-arctic-medium", "speaker": "rms"}
