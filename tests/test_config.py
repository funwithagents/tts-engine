"""Tests for config loading and validation."""
import json

import pytest

from tts_mcp.config import ConfigError, load_config


def _write_config(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


VALID = {
    "tts": {
        "type": "elevenlabs",
        "api_key": "sk_test",
        "voice_id": "abc123",
        "model": "eleven_flash_v2_5",
        "stability": 0.5,
        "similarity_boost": 0.75,
    },
    "audio": {"device": None},
    "server": {"host": "127.0.0.1", "port": 8000},
}


def test_valid_config(tmp_path):
    tts, audio, server = load_config(_write_config(tmp_path, VALID))
    assert tts.type == "elevenlabs"
    assert tts.raw["api_key"] == "sk_test"
    assert audio.device is None
    assert server.host == "127.0.0.1"
    assert server.port == 8000


def test_extra_tts_fields_preserved(tmp_path):
    data = {**VALID, "tts": {**VALID["tts"], "custom_field": "hello"}}
    tts, _, _ = load_config(_write_config(tmp_path, data))
    assert tts.raw["custom_field"] == "hello"


def test_missing_tts_block(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "tts"}
    with pytest.raises(ConfigError, match="tts"):
        load_config(_write_config(tmp_path, data))


def test_missing_audio_block(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "audio"}
    with pytest.raises(ConfigError, match="audio"):
        load_config(_write_config(tmp_path, data))


def test_missing_server_block(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "server"}
    with pytest.raises(ConfigError, match="server"):
        load_config(_write_config(tmp_path, data))


def test_tts_type_missing(tmp_path):
    data = {**VALID, "tts": {"api_key": "sk_test"}}
    with pytest.raises(ConfigError, match="tts.type"):
        load_config(_write_config(tmp_path, data))


def test_tts_type_empty(tmp_path):
    data = {**VALID, "tts": {**VALID["tts"], "type": ""}}
    with pytest.raises(ConfigError, match="tts.type"):
        load_config(_write_config(tmp_path, data))


def test_invalid_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    with pytest.raises(ConfigError, match=str(p)):
        load_config(str(p))


def test_port_out_of_range(tmp_path):
    data = {**VALID, "server": {"host": "127.0.0.1", "port": 99999}}
    with pytest.raises(ConfigError, match="port"):
        load_config(_write_config(tmp_path, data))


def test_port_zero(tmp_path):
    data = {**VALID, "server": {"host": "127.0.0.1", "port": 0}}
    with pytest.raises(ConfigError, match="port"):
        load_config(_write_config(tmp_path, data))
