"""Tests for the `tts-engine-calibrate` entry point, on the `tone` fixture:
the config forms it reads, its output, its exit codes."""

import json

import pytest

from tts_engine.calibrate_cli import _LOG_FORMAT, main


def _write(tmp_path, data, name="engine.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


_TONE = {"module": {"type": "tone", "seconds_per_char": 0.01}}


def test_prints_the_level_and_the_suggested_gain(tmp_path, capsys, mocker):
    basic_config = mocker.patch("tts_engine.calibrate_cli.logging.basicConfig")
    assert main(["--config", _write(tmp_path, _TONE), "--log-level", "INFO"]) == 0

    basic_config.assert_called_once_with(level="INFO", format=_LOG_FORMAT)
    out, err = capsys.readouterr()
    assert "3 text(s)" in err and "tone module" in err  # what is synthesized
    lines = dict(line.split(None, 1) for line in out.splitlines() if " " in line)
    assert lines["level"] == "-17.0 dBFS (active speech)"
    assert lines["gain_db"] == "-5.0"


def test_json_output_reads_an_mcp_server_config(tmp_path, capsys):
    path = _write(tmp_path, {"engine": {**_TONE, "gain_db": -3}}, "config.json")
    assert main(["--config", path, "--json", "--target", "-18", "--text", "Hi."]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["level_dbfs"] == -17.0
    assert result["peak_dbfs"] == -14.0
    assert (result["target_dbfs"], result["gain_db"]) == (-18.0, -1.0)
    assert result["peak_after_dbfs"] == -15.0
    assert result["clips"] is False
    assert result["limited"] is False
    assert result["current_gain_db"] == -3.0
    assert result["duration_s"] == pytest.approx(0.03, abs=0.01)  # one text only


def test_warns_when_the_suggested_gain_would_clip(tmp_path, capsys):
    # The sine's peak is 3 dB above its level: a target of -1 puts it at +2.
    assert main(["--config", _write(tmp_path, _TONE), "--target", "-1"]) == 0
    assert "would clip" in capsys.readouterr().err


def test_warns_when_the_field_range_stops_the_gain(tmp_path, capsys):
    quiet = {"module": {**_TONE["module"], "amplitude": 0.001}}  # about -63 dBFS
    path = _write(tmp_path, quiet)
    # Too quiet to be active speech at all: nothing to measure.
    assert main(["--config", path]) == 2
    assert "no active speech" in capsys.readouterr().err

    quiet["module"]["amplitude"] = 0.02  # -37 dBFS: +24 dB stops short of -1
    assert main(["--config", _write(tmp_path, quiet), "--target", "-1"]) == 0
    assert "the field's limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("content", "args", "message"),
    [
        ("{not json", [], "Cannot read config file"),
        (json.dumps({"module": {"type": "nope"}}), [], "nope"),
        (json.dumps(_TONE), ["--target", "5"], "target_dbfs"),
    ],
)
def test_a_bad_config_or_target_exits_2(tmp_path, capsys, content, args, message):
    path = tmp_path / "engine.json"
    path.write_text(content, encoding="utf-8")
    assert main(["--config", str(path), *args]) == 2
    assert message in capsys.readouterr().err
