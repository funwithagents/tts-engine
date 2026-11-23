"""`tts_engine.setup`: setting the engine up from an application, as data.

See specs/setup.md.
"""

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

import pytest
from module_setup.contract import check_contract

from tts_engine import setup
from tts_engine.config import ConfigError
from tts_engine.modules import REGISTRY
from tts_engine.modules.base import VoiceInfo, VoiceListingUnsupported
from tts_engine.modules.kokoro import KokoroModule
from tts_engine.modules.tone import ToneModule

_ROOT = Path(__file__).resolve().parent.parent


def _examples() -> dict[str, dict[str, Any]]:
    """The engine block of every example config, by file name."""
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))["engine"]
        for path in sorted((_ROOT / "examples").glob("config.*.json"))
    }


# --- The declarations ---------------------------------------------------------


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_module_declares_its_title_its_extra_and_documented_fields(name: str) -> None:
    cls = REGISTRY[name]
    extras = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]

    assert cls.title
    assert cls.extra is None or cls.extra in extras
    assert (cls.extra is None) == (cls.kind == "fixture")
    assert all(field.description.endswith(".") for field in cls.fields)
    assert len({field.name for field in cls.fields}) == len(cls.fields)


def test_describe_is_stable_and_offers_one_branch_per_registered_module() -> None:
    description = setup.describe()

    assert description == setup.describe()
    assert (description["contract"], description["distribution"]) == (1, "tts-engine")
    branches = description["schema"]["properties"]["module"]["oneOf"]
    assert [b["properties"]["type"]["const"] for b in branches] == list(REGISTRY)
    eleven = next(
        b for b in branches if b["properties"]["type"]["const"] == "elevenlabs"
    )
    assert eleven["title"] == "ElevenLabs (hosted)"
    assert eleven["required"] == ["voice_id"]
    assert eleven["properties"]["api_key_env"]["x-secret-env"] is True
    assert eleven["properties"]["api_key"]["x-secret"] is True


def test_the_contract_s_conformance_check_passes_over_the_examples() -> None:
    examples = list(_examples().values())

    check_contract(setup, examples)


# --- requirements -------------------------------------------------------------


def test_requirements_names_the_extra_of_the_selected_module() -> None:
    assert setup.requirements({"module": {"type": "kokoro"}}) == ["tts-engine[kokoro]"]
    assert setup.requirements({"module": {"type": "elevenlabs"}}) == [
        "tts-engine[elevenlabs]"
    ]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"module": {"type": "tone"}},
        {"module": {"type": "no-such-module"}},
        {"module": "tone"},
        {"module": {"type": 3}},
        [],
    ],
)
def test_requirements_is_empty_when_nothing_is_needed_or_selected(config: Any) -> None:
    assert setup.requirements(config) == []


# --- validate -----------------------------------------------------------------


def test_every_example_config_is_accepted() -> None:
    refused = {
        name: problems
        for name, block in _examples().items()
        if (problems := setup.validate(block))
    }

    assert refused == {}


ENGINE_PROBLEMS = [
    ([], ""),
    ({}, "/module"),
    ({"module": {}}, "/module/type"),
    ({"module": {"type": "tone", "base_dir": ""}}, "/module/base_dir"),
    ({"module": {"type": "nope"}}, "/module/type"),
    ({"module": {"type": "tone"}, "player": 3}, "/player"),
    ({"module": {"type": "tone"}, "player": {"device": 1.5}}, "/player/device"),
    ({"module": {"type": "tone"}, "player": {"discard": "yes"}}, "/player/discard"),
    (
        {"module": {"type": "tone"}, "player": {"device": "x", "discard": True}},
        "/player/discard",
    ),
    ({"module": {"type": "tone"}, "gain_db": 100}, "/gain_db"),
]


@pytest.mark.parametrize(("config", "path"), ENGINE_PROBLEMS)
def test_an_engine_level_problem_is_reported_at_its_path(
    config: Any, path: str
) -> None:
    problems = setup.validate(config)

    assert [problem["path"] for problem in problems] == [path]
    assert problems[0]["message"]


def test_engine_and_module_problems_are_reported_together() -> None:
    problems = setup.validate(
        {"module": {"type": "tone", "frequency": -1, "amplitude": 2}, "gain_db": 100}
    )

    assert [problem["path"] for problem in problems] == [
        "/gain_db",
        "/module/frequency",
        "/module/amplitude",
    ]


# What `validate` refuses, the constructor refuses with the same message.
MODULE_PROBLEMS = [
    ({"type": "tone", "sample_rate": 0}, "/module/sample_rate"),
    ({"type": "tone", "amplitude": 1.5}, "/module/amplitude"),
    ({"type": "elevenlabs"}, "/module/voice_id"),
    ({"type": "elevenlabs", "voice_id": "v", "stability": 2}, "/module/stability"),
    ({"type": "elevenlabs", "voice_id": "v", "api_key": 5}, "/module/api_key"),
    ({"type": "elevenlabs", "voice_id": "v", "model": None}, "/module/model"),
    ({"type": "kokoro", "speed": 9}, "/module/speed"),
    ({"type": "kokoro", "threads": 0}, "/module/threads"),
    ({"type": "kokoro", "voice": "xx_unknown"}, "/module"),
    ({"type": "kokoro", "model": "hf://only-a-repo"}, "/module"),
    ({"type": "gradium"}, "/module/voice_id"),
    ({"type": "gradium", "voice_id": "v", "sample_rate": 22050}, "/module/sample_rate"),
    ({"type": "gradium", "voice_id": "v", "temp": 2}, "/module/temp"),
    ({"type": "mistral"}, "/module/voice_id"),
    (
        {"type": "mistral", "voice_id": "v", "expressiveness": 0.5},
        "/module/expressiveness",
    ),
    ({"type": "deepgram", "speed": 0.6}, "/module/speed"),
    ({"type": "deepgram", "model": "flux-haley-en", "speed": 0.53}, "/module"),
    ({"type": "deepgram", "expressivity": 1}, "/module/expressivity"),
    (
        {"type": "deepgram", "model": "flux-haley-en", "expressivity": 3},
        "/module/expressivity",
    ),
    ({"type": "gemini", "voice": ""}, "/module/voice"),
    ({"type": "gemini", "api_key": 5}, "/module/api_key"),
    ({"type": "cartesia"}, "/module/voice_id"),
    (
        {"type": "cartesia", "voice_id": "v", "sample_rate": 12345},
        "/module/sample_rate",
    ),
    (
        {"type": "cartesia", "voice_id": "v", "max_buffer_delay_ms": 5001},
        "/module/max_buffer_delay_ms",
    ),
    ({"type": "openai", "voice": None}, "/module/voice"),
    ({"type": "openai", "speed": 2}, "/module/speed"),
    ({"type": "openai-speech-compatible"}, "/module/voice"),
    (
        {"type": "openai-speech-compatible", "voice": "marin", "sample_rate": 16000},
        "/module",
    ),
    ({"type": "piper", "speaker": -1}, "/module/speaker"),
    ({"type": "piper", "speaker": True}, "/module/speaker"),
    ({"type": "piper", "voice": "a-b-c", "model": "x.onnx"}, "/module"),
    ({"type": "pocket", "device": "gpu"}, "/module/device"),
    ({"type": "pocket", "voice": None}, "/module/voice"),
    ({"type": "pocket", "quantize": True, "device": "cuda"}, "/module"),
    ({"type": "phonon", "language": "it"}, "/module/language"),
    ({"type": "phonon", "device": None}, "/module/device"),
    ({"type": "qwen3", "temperature": 0}, "/module/temperature"),
    ({"type": "qwen3", "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base"}, "/module"),
    ({"type": "audiofile"}, "/module"),
    ({"type": "audiofile", "files": "a.wav"}, "/module/files"),
    ({"type": "audiofile", "default_file": None, "files": []}, "/module/default_file"),
    ({"type": "audiofile", "files": [{"text": "a"}]}, "/module"),
]


@pytest.mark.parametrize(("module", "path"), MODULE_PROBLEMS)
def test_validate_and_the_constructor_refuse_the_same_config_with_the_same_message(
    module: dict[str, Any], path: str
) -> None:
    problems = setup.validate({"module": module})

    assert [problem["path"] for problem in problems] == [path]
    with pytest.raises(ConfigError) as error:
        REGISTRY[module["type"]](module)
    assert str(error.value) == problems[0]["message"]


def test_a_config_validate_accepts_is_one_the_constructor_builds() -> None:
    module = {"type": "tone", "frequency": 220, "amplitude": 1}

    assert setup.validate({"module": module}) == []
    assert ToneModule(module).sample_rate == 24000


def test_a_renamed_type_is_accepted_though_the_schema_does_not_offer_it() -> None:
    module = {"type": "openai-compatible", "voice": "marin"}

    assert setup.validate({"module": module}) == []
    assert setup.requirements({"module": module}) == ["tts-engine[openai-compatible]"]


_NO_PROVIDER_SCRIPT = textwrap.dedent(
    """
    import sys

    for name in (
        "elevenlabs", "miniaudio", "pocket_tts", "torch", "gradium", "ptts",
        "mistralai", "deepgram", "google.genai", "cartesia", "openai",
        "qwentts_cpp", "kokoro_onnx", "piper", "huggingface_hub", "onnxruntime",
        "sounddevice",
    ):
        sys.modules[name] = None

    from tts_engine import setup

    setup.describe()
    for module in ({"type": "tone"}, {"type": "elevenlabs", "voice_id": "v"},
                   {"type": "kokoro", "voice": "af_heart"}):
        assert setup.validate({"module": module}) == [], module
    assert setup.validate({"module": {"type": "kokoro", "speed": 9}})
    assert setup.requirements({"module": {"type": "kokoro"}}) == ["tts-engine[kokoro]"]
    print("OK")
    """
)


def test_describe_validate_and_requirements_need_no_provider_library() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _NO_PROVIDER_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- check and prepare ----------------------------------------------------------


def test_a_module_without_files_has_no_item_and_nothing_to_prepare() -> None:
    config = {"module": {"type": "tone"}}

    assert setup.check(config) == []
    setup.prepare(config)


def test_kokoro_s_files_are_missing_until_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import huggingface_hub

    cache: set[str] = set()
    downloads: list[tuple[str, str, str | None]] = []

    def cached(repo_id: str, filename: str, revision: str | None = None) -> str | None:
        return f"/cache/{filename}" if filename in cache else None

    def download(repo_id: str, filename: str, revision: str | None = None) -> str:
        downloads.append((repo_id, filename, revision))
        cache.add(filename)
        return f"/cache/{filename}"

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", cached)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    config = {"module": {"type": "kokoro"}}

    before = setup.check(config)
    assert [(item["id"], item["status"]) for item in before] == [
        ("kokoro:model", "missing"),
        ("kokoro:voices", "missing"),
    ]
    assert all(item["preparable"] for item in before)

    progress: list[dict[str, Any]] = []
    setup.prepare(config, progress.append)

    assert [d[:2] for d in downloads] == [
        ("fastrtc/kokoro-onnx", "kokoro-v1.0.onnx"),
        ("fastrtc/kokoro-onnx", "voices-v1.0.bin"),
    ]
    assert all(revision for _, _, revision in downloads)
    assert [p["id"] for p in progress if p["fraction"] == 1.0] == [
        "kokoro:model",
        "kokoro:voices",
    ]
    assert [item["status"] for item in setup.check(config)] == ["ready", "ready"]

    setup.prepare(config)  # everything is ready: nothing more is fetched
    assert len(downloads) == 2


def test_a_local_file_is_ready_or_missing_and_never_preparable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub

    monkeypatch.setattr(
        huggingface_hub, "try_to_load_from_cache", lambda *a, **k: "/cache/voices"
    )
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda *a, **k: pytest.fail("a local file is not downloaded"),
    )
    model = tmp_path / "model.onnx"
    config = {"module": {"type": "kokoro", "model": str(model)}}

    missing = setup.check(config)[0]
    assert (missing["status"], missing["preparable"]) == ("missing", False)
    assert str(model) in missing["detail"]
    setup.prepare(config)

    model.write_bytes(b"onnx")
    assert setup.check(config)[0]["status"] == "ready"


# --- options --------------------------------------------------------------------


def test_voices_are_offered_with_the_fields_that_select_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voices = [
        VoiceInfo(
            "v1", "River", {"voice_id": "v1", "model": "m2"}, ("en", "fr"), "neutral"
        ),
        VoiceInfo("v2", "Plain", {"voice_id": "v2"}),
    ]
    seen: list[dict[str, Any]] = []

    def list_voices(config: dict[str, Any]) -> list[VoiceInfo]:
        seen.append(config)
        return voices

    monkeypatch.setattr(ToneModule, "list_voices", list_voices)
    module = {"type": "tone"}

    assert setup.options("voices", {"module": module}) == [
        {
            "label": "River (en, fr, neutral)",
            "fields": {"voice_id": "v1", "model": "m2"},
        },
        {"label": "Plain", "fields": {"voice_id": "v2"}},
    ]
    assert seen == [module]


def test_a_module_that_cannot_list_its_voices_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(config: dict[str, Any]) -> list[VoiceInfo]:
        raise VoiceListingUnsupported("see the provider's voice library")

    monkeypatch.setattr(KokoroModule, "list_voices", unsupported)

    with pytest.raises(VoiceListingUnsupported, match="voice library"):
        setup.options("voices", {"module": {"type": "kokoro"}})


def test_output_devices_are_the_machine_s_outputs_after_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sounddevice

    monkeypatch.setattr(
        sounddevice,
        "query_devices",
        lambda: [
            {"name": "Microphone", "max_output_channels": 0},
            {"name": "Speakers", "max_output_channels": 2},
        ],
    )

    assert setup.options("output-devices", {}) == [
        {"label": "System default", "fields": {"device": None}},
        {"label": "Speakers", "fields": {"device": "Speakers"}},
    ]


def test_an_unknown_list_is_empty() -> None:
    assert setup.options("no-such-list", {"module": {"type": "tone"}}) == []


# --- probe ----------------------------------------------------------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.audio = b""
        self.drains = 0

    def feed(self, chunk: bytes) -> None:
        self.audio += chunk

    def drain(self) -> None:
        self.drains += 1


def test_probe_speaks_a_sentence_and_releases_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    monkeypatch.setattr(
        ToneModule, "close", lambda self: closed.append("module"), raising=False
    )
    sink = _RecordingSink()

    result = setup.probe({"module": {"type": "tone"}}, sink=sink)

    assert result["ok"] is True
    assert "nothing was played" in result["message"]
    assert result["duration_s"] >= 0
    # The whole sentence, at 0.05 s a character of 24 kHz 16-bit mono.
    assert len(sink.audio) == round(len(setup.PROBE_SENTENCE) * 0.05 * 24000) * 2
    assert sink.drains == 1
    assert closed == ["module"]


def test_probe_reports_a_config_that_cannot_run_and_raises_nothing() -> None:
    result = setup.probe({"module": {"type": "elevenlabs"}}, sink=_RecordingSink())

    assert result["ok"] is False
    assert result["message"] == "ElevenLabs module requires a non-empty 'voice_id'"


def test_probe_runs_from_a_thread_that_already_has_an_event_loop() -> None:
    import asyncio

    async def on_a_loop() -> dict[str, Any]:
        return setup.probe({"module": {"type": "tone"}}, sink=_RecordingSink())

    assert asyncio.run(on_a_loop())["ok"] is True
