"""Unit tier for tools/voice-audition/audition.py: the audition file, `new`
against a faked `list_voices`, `render` through the `tone` fixture, the page.
No network, no key, no audio hardware."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import wave
from pathlib import Path

import pytest

from tts_engine import VoiceInfo, VoiceListingUnsupported

_AUDITION_PY = (
    Path(__file__).resolve().parents[1] / "tools" / "voice-audition" / "audition.py"
)
_spec = importlib.util.spec_from_file_location("voice_audition", _AUDITION_PY)
assert _spec is not None and _spec.loader is not None
audition = importlib.util.module_from_spec(_spec)
sys.modules["voice_audition"] = audition
_spec.loader.exec_module(audition)


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _tone_audition(tmp_path: Path, **overrides) -> Path:
    data = {
        "text": "Hello there.",
        "candidates": [
            {"id": "tone-a", "config": {"type": "tone"}},
            {
                "id": "tone-b",
                "label": "Second",
                "section": "fixtures",
                "config": {"type": "tone"},
            },
        ],
        **overrides,
    }
    return _write(tmp_path / "trial.json", data)


# --- the audition file --------------------------------------------------------


def test_load_fills_in_the_defaults(tmp_path):
    loaded = audition.load_audition(_tone_audition(tmp_path))
    assert loaded["name"] == "trial"
    first, second = loaded["candidates"]
    assert (first["label"], first["section"], first["notes"]) == ("tone-a", "tone", "")
    assert (second["label"], second["section"]) == ("Second", "fixtures")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"text": "  "}, "'text'"),
        ({"surprise": 1}, "unknown keys"),
        (
            {"candidates": [{"id": "Bad Id", "config": {"type": "tone"}}]},
            "'id' must match",
        ),
        (
            {
                "candidates": [
                    {"id": "a", "config": {"type": "tone"}},
                    {"id": "a", "config": {"type": "tone"}},
                ]
            },
            "duplicate id 'a'",
        ),
        ({"candidates": [{"id": "a", "config": {"voice": "x"}}]}, "'type'"),
        (
            {"candidates": [{"id": "a", "config": {"type": "tone"}, "colour": "red"}]},
            "unknown keys",
        ),
        (
            {"candidates": [{"id": "a", "config": {"type": "tone"}, "notes": 3}]},
            "'notes'",
        ),
    ],
)
def test_load_rejects_an_invalid_file_naming_the_field(tmp_path, change, message):
    with pytest.raises(audition.AuditionError, match=message):
        audition.load_audition(_tone_audition(tmp_path, **change))


# --- new ------------------------------------------------------------------------


def test_parse_module_gives_a_hosted_type_its_conventional_key_variable():
    assert audition.parse_module("cartesia") == {
        "type": "cartesia",
        "api_key_env": "CARTESIA_API_KEY",
    }
    assert (
        audition.parse_module("openai-compatible")["api_key_env"]
        == "OPENAI_COMPATIBLE_API_KEY"
    )
    assert audition.parse_module("phonon") == {"type": "phonon"}
    assert audition.parse_module('{"type": "qwen3", "model": "m"}') == {
        "type": "qwen3",
        "model": "m",
    }
    with pytest.raises(audition.AuditionError, match="unknown module type"):
        audition.parse_module("nope")


def _voice(
    name, *, gender=None, languages=(), description=None, tags=(), **config
) -> VoiceInfo:
    return VoiceInfo(
        id=name,
        name=name,
        config=config or {"voice": name},
        languages=tuple(languages),
        gender=gender,
        description=description,
        tags=tuple(tags),
    )


@pytest.mark.parametrize(
    ("voice", "filters", "expected"),
    [
        (_voice("a", gender="male"), {"genders": ["male", "neutral"]}, True),
        (_voice("a", gender="female"), {"genders": ["male"]}, False),
        (_voice("a"), {"genders": ["male"]}, False),
        (_voice("a"), {"genders": ["male"], "keep_unknown": True}, True),
        (_voice("a", languages=["en_GB"]), {"languages": ["en"]}, True),
        (_voice("a", languages=["en-us"]), {"languages": ["EN-US"]}, True),
        (_voice("a", languages=["eng"]), {"languages": ["en"]}, False),
        (_voice("a"), {"languages": ["en"]}, False),
        (_voice("Zeke", tags=["Sidekick"]), {"query": "sidekick"}, True),
        (_voice("Zeke", description="calm"), {"query": "loud"}, False),
    ],
)
def test_voice_matches(voice, filters, expected):
    kwargs = {
        "genders": [],
        "languages": [],
        "query": None,
        "keep_unknown": False,
        **filters,
    }
    assert audition.voice_matches(voice, **kwargs) is expected


def _new_args(tmp_path: Path, *modules: str, **options) -> list[str]:
    argv = ["new", str(tmp_path / "picked.json"), "--text", "Hi."]
    for module in modules:
        argv += ["--module", module]
    for key, value in options.items():
        flag = "--" + key.replace("_", "-")
        argv += [flag] if value is True else [flag, str(value)]
    return argv


def test_new_writes_candidates_from_the_catalogs(tmp_path, monkeypatch, capsys):
    catalogs = {
        "cartesia": [
            _voice(
                "Zeke",
                gender="male",
                languages=["en"],
                description="Sidekick",
                voice_id="z1",
            ),
            _voice("Ann", gender="female", languages=["en"], voice_id="a1"),
            _voice("Zeke", gender="male", languages=["en"], voice_id="z2"),
            _voice("Max", gender="male", languages=["en"], voice_id="m1"),
        ],
        "phonon": [_voice("Micah", languages=["en"])],
    }
    seen: list[dict] = []

    def fake_list_voices(config):
        seen.append(config)
        if config["type"] == "gemini":
            raise VoiceListingUnsupported("see the docs")
        return catalogs[config["type"]]

    monkeypatch.setattr(audition, "list_voices", fake_list_voices)
    argv = _new_args(
        tmp_path,
        "cartesia",
        "phonon",
        "gemini",
        gender="male",
        limit=2,
        keep_unknown=True,
    )
    assert audition.main(argv) == 0

    assert seen[0] == {"type": "cartesia", "api_key_env": "CARTESIA_API_KEY"}
    written = json.loads((tmp_path / "picked.json").read_text(encoding="utf-8"))
    assert written["name"] == "picked" and written["text"] == "Hi."
    ids = [c["id"] for c in written["candidates"]]
    assert ids == [
        "cartesia-zeke",
        "cartesia-zeke-2",
        "phonon-micah",
    ]  # limit 2 per module
    zeke = written["candidates"][0]
    assert zeke["config"] == {
        "type": "cartesia",
        "api_key_env": "CARTESIA_API_KEY",
        "voice_id": "z1",
    }
    assert (zeke["label"], zeke["section"], zeke["notes"]) == (
        "Zeke",
        "cartesia",
        "male · en · Sidekick",
    )
    assert "gemini: cannot list voices: see the docs" in capsys.readouterr().out
    # the result is a valid audition file
    assert len(audition.load_audition(tmp_path / "picked.json")["candidates"]) == 3


def test_new_refuses_to_overwrite_and_reports_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(
        audition, "list_voices", lambda config: [_voice("a", gender="female")]
    )
    (tmp_path / "picked.json").write_text("{}")
    assert audition.main(_new_args(tmp_path, "phonon")) == 2
    assert audition.main(_new_args(tmp_path, "phonon", gender="male", force=True)) == 1
    assert (tmp_path / "picked.json").read_text(encoding="utf-8") == "{}"


# --- render ---------------------------------------------------------------------


def _render(path: Path, **kwargs) -> dict:
    return asyncio.run(audition.render(path, audition.default_out_dir(path), **kwargs))


def test_render_writes_samples_measurements_and_the_page(tmp_path):
    path = _tone_audition(tmp_path)
    results = _render(path, runs=2)

    out = tmp_path / "trial.audition"
    entry = results["tone-a"]
    assert entry["status"] == "ok"
    assert len(entry["ttfa_ms"]) == 2
    assert min(entry["ttfa_ms"]) <= entry["ttfa_median_ms"] <= max(entry["ttfa_ms"])
    with wave.open(str(out / "samples" / "tone-a.wav")) as wav:
        assert (wav.getnchannels(), wav.getsampwidth()) == (1, 2)
        assert wav.getframerate() == entry["sample_rate"]
        assert wav.getnframes() / wav.getframerate() == pytest.approx(
            entry["duration_s"], abs=0.01
        )
    assert json.loads((out / "results.json").read_text(encoding="utf-8")) == results
    assert (out / "index.html").exists()


def test_render_only_renders_again_what_changed(tmp_path, monkeypatch):
    path = _tone_audition(tmp_path)
    _render(path, runs=1)
    rendered: list[str] = []
    real = audition.render_candidate

    async def counting(candidate, *args, **kwargs):
        rendered.append(candidate["id"])
        return await real(candidate, *args, **kwargs)

    monkeypatch.setattr(audition, "render_candidate", counting)
    _render(path, runs=1)
    assert rendered == []  # nothing changed

    _render(path, runs=1, force=True)
    assert rendered == ["tone-a", "tone-b"]

    rendered.clear()
    _render(path, runs=1, only=["tone-b"], force=True)
    assert rendered == ["tone-b"]

    rendered.clear()
    _tone_audition(tmp_path, text="Something else.")
    _render(path, runs=1)
    assert rendered == ["tone-a", "tone-b"]  # the text is part of the key


def test_render_skips_an_unset_key_and_records_a_failing_config(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDITION_TEST_KEY", raising=False)
    path = _write(
        tmp_path / "mixed.json",
        {
            "text": "Hi.",
            "candidates": [
                {
                    "id": "hosted",
                    "config": {
                        "type": "cartesia",
                        "api_key_env": "AUDITION_TEST_KEY",
                        "voice_id": "v",
                    },
                },
                {"id": "broken", "config": {"type": "nope"}},
                {"id": "fine", "config": {"type": "tone"}},
            ],
        },
    )
    assert (
        audition.main(["render", str(path), "--runs", "1"]) == 1
    )  # an error fails the command

    results = json.loads(
        (tmp_path / "mixed.audition" / "results.json").read_text(encoding="utf-8")
    )
    assert results["hosted"]["status"] == "skipped"
    assert results["hosted"]["reason"] == "AUDITION_TEST_KEY is not set"
    assert results["broken"]["status"] == "error"
    assert "Unknown TTS module type" in results["broken"]["error"]
    assert results["fine"]["status"] == "ok"


def test_render_drops_results_of_removed_candidates(tmp_path):
    path = _tone_audition(tmp_path)
    _render(path, runs=1)
    _tone_audition(tmp_path, candidates=[{"id": "tone-a", "config": {"type": "tone"}}])
    assert set(_render(path, runs=1)) == {"tone-a"}


def test_render_rejects_an_unknown_only_id_and_zero_runs(tmp_path):
    path = _tone_audition(tmp_path)
    with pytest.raises(audition.AuditionError, match="no candidate"):
        _render(path, only=["ghost"])
    with pytest.raises(audition.AuditionError, match="at least 1"):
        _render(path, runs=0)


# --- page -----------------------------------------------------------------------


def _embedded_data(page: str) -> dict:
    start = page.index("const DATA = ") + len("const DATA = ")
    end = page.index(";\n", start)
    return json.loads(page[start:end].replace("<\\/", "</"))


def test_page_embeds_the_data_without_secrets(tmp_path):
    path = _write(
        tmp_path / "secret.json",
        {
            "name": "trial </script> name",
            "text": "Hi.",
            "candidates": [
                {
                    "id": "a",
                    "notes": "n",
                    "config": {"type": "tone", "api_key": "sk-123"},
                }
            ],
        },
    )
    assert audition.main(["page", str(path)]) == 0
    page = (tmp_path / "secret.audition" / "index.html").read_text(encoding="utf-8")

    assert "sk-123" not in page
    assert "</script> name" not in page  # escaped inside the script element
    data = _embedded_data(page)
    assert data["name"] == "trial </script> name"
    assert data["sections"] == ["tone"]
    (candidate,) = data["candidates"]
    assert candidate["config"] == {"type": "tone", "api_key": "***"}
    assert candidate["result"] == {}  # not rendered yet
    assert 'const KEY = "voice-audition:" + DATA.name;' in page


def test_example_audition_is_valid():
    example = _AUDITION_PY.parent / "examples" / "demo.json"
    loaded = audition.load_audition(example)
    assert loaded["candidates"]
