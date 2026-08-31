"""Tests for config loading and validation."""

import json

import pytest

from tts_engine.config import AppConfig, ConfigError, load_config


def _write_config(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


VALID = {
    "engine": {
        "module": {
            "type": "elevenlabs",
            "api_key": "sk_test",
            "voice_id": "abc123",
            "model": "eleven_flash_v2_5",
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
        "player": {"device": None},
    },
    "server": {"host": "127.0.0.1", "port": 8000},
    "logging": {"level": "INFO"},
}


def test_valid_config(tmp_path):
    cfg = load_config(_write_config(tmp_path, VALID))
    assert isinstance(cfg, AppConfig)
    assert cfg.engine.module["type"] == "elevenlabs"
    assert cfg.engine.module["api_key"] == "sk_test"
    assert cfg.engine.player.device is None
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.logging.level == "INFO"


def test_extra_module_fields_preserved(tmp_path):
    data = {
        **VALID,
        "engine": {
            **VALID["engine"],
            "module": {**VALID["engine"]["module"], "custom_field": "hello"},
        },
    }
    cfg = load_config(_write_config(tmp_path, data))
    assert cfg.engine.module["custom_field"] == "hello"


def test_server_and_logging_default_when_omitted(tmp_path):
    data = {"engine": VALID["engine"]}
    cfg = load_config(_write_config(tmp_path, data))
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.logging.level == "INFO"


def test_player_defaults_when_omitted(tmp_path):
    data = {**VALID, "engine": {"module": VALID["engine"]["module"]}}
    cfg = load_config(_write_config(tmp_path, data))
    assert cfg.engine.player.device is None


def test_missing_engine_block(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "engine"}
    with pytest.raises(ConfigError, match="engine"):
        load_config(_write_config(tmp_path, data))


def test_missing_module_block(tmp_path):
    data = {**VALID, "engine": {"player": {"device": None}}}
    with pytest.raises(ConfigError, match="engine.module"):
        load_config(_write_config(tmp_path, data))


def test_module_type_missing(tmp_path):
    data = {**VALID, "engine": {"module": {"api_key": "sk_test"}}}
    with pytest.raises(ConfigError, match="engine.module.type"):
        load_config(_write_config(tmp_path, data))


def test_module_type_empty(tmp_path):
    data = {**VALID, "engine": {"module": {**VALID["engine"]["module"], "type": ""}}}
    with pytest.raises(ConfigError, match="engine.module.type"):
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


def test_unknown_logging_level(tmp_path):
    data = {**VALID, "logging": {"level": "LOUD"}}
    with pytest.raises(ConfigError, match="logging.level"):
        load_config(_write_config(tmp_path, data))


def test_logging_level_normalized_to_upper(tmp_path):
    data = {**VALID, "logging": {"level": "debug"}}
    cfg = load_config(_write_config(tmp_path, data))
    assert cfg.logging.level == "DEBUG"
