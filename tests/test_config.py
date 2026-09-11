"""Tests for config parsing and validation.

Both public configs expose a `from_dict` / `from_json` / `from_json_file` trio.
`MCPServerConfig` parses the top-level `{engine, server}` file; `TTSEngineConfig`
parses an `engine` block (module + player) on its own.
"""

import json

import pytest

from tts_engine.config import (
    ConfigError,
    MCPServerConfig,
    TTSEngineConfig,
)


def _write(tmp_path, data):
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
}


# --- MCPServerConfig.from_json_file / from_json / from_dict ----------------


def test_valid_config(tmp_path):
    cfg = MCPServerConfig.from_json_file(_write(tmp_path, VALID))
    assert isinstance(cfg, MCPServerConfig)
    assert cfg.engine.module["type"] == "elevenlabs"
    assert cfg.engine.module["api_key"] == "sk_test"
    assert cfg.engine.player.device is None
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000


def test_from_json_matches_from_json_file(tmp_path):
    from_str = MCPServerConfig.from_json(json.dumps(VALID))
    from_file = MCPServerConfig.from_json_file(_write(tmp_path, VALID))
    assert from_str == from_file


def test_from_dict_top_level(tmp_path):
    assert MCPServerConfig.from_dict(VALID) == MCPServerConfig.from_json_file(
        _write(tmp_path, VALID)
    )


def test_extra_module_fields_preserved(tmp_path):
    data = {
        **VALID,
        "engine": {
            **VALID["engine"],
            "module": {**VALID["engine"]["module"], "custom_field": "hello"},
        },
    }
    cfg = MCPServerConfig.from_json_file(_write(tmp_path, data))
    assert cfg.engine.module["custom_field"] == "hello"


def test_server_defaults_when_omitted(tmp_path):
    data = {"engine": VALID["engine"]}
    cfg = MCPServerConfig.from_json_file(_write(tmp_path, data))
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000


def test_player_defaults_when_omitted(tmp_path):
    data = {**VALID, "engine": {"module": VALID["engine"]["module"]}}
    cfg = MCPServerConfig.from_json_file(_write(tmp_path, data))
    assert cfg.engine.player.device is None


def test_missing_engine_block(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "engine"}
    with pytest.raises(ConfigError, match="engine"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_missing_module_block(tmp_path):
    data = {**VALID, "engine": {"player": {"device": None}}}
    with pytest.raises(ConfigError, match="engine.module"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_module_type_missing(tmp_path):
    data = {**VALID, "engine": {"module": {"api_key": "sk_test"}}}
    with pytest.raises(ConfigError, match="engine.module.type"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_module_type_empty(tmp_path):
    data = {**VALID, "engine": {"module": {**VALID["engine"]["module"], "type": ""}}}
    with pytest.raises(ConfigError, match="engine.module.type"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_invalid_json_file_names_path(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    with pytest.raises(ConfigError, match=str(p)):
        MCPServerConfig.from_json_file(str(p))


def test_invalid_json_string():
    with pytest.raises(ConfigError, match="Invalid JSON"):
        MCPServerConfig.from_json("{not valid json")


def test_top_level_not_object():
    with pytest.raises(ConfigError, match="config must be an object"):
        MCPServerConfig.from_json("[]")


def test_server_not_object(tmp_path):
    data = {**VALID, "server": "127.0.0.1:8000"}
    with pytest.raises(ConfigError, match="server"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_port_out_of_range(tmp_path):
    data = {**VALID, "server": {"host": "127.0.0.1", "port": 99999}}
    with pytest.raises(ConfigError, match="port"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_port_zero(tmp_path):
    data = {**VALID, "server": {"host": "127.0.0.1", "port": 0}}
    with pytest.raises(ConfigError, match="port"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


def test_port_bool_rejected(tmp_path):
    data = {**VALID, "server": {"host": "127.0.0.1", "port": True}}
    with pytest.raises(ConfigError, match="port"):
        MCPServerConfig.from_json_file(_write(tmp_path, data))


@pytest.mark.parametrize("bad", ["", 123, None])
def test_host_invalid_rejected(bad):
    data = {**VALID, "server": {"host": bad, "port": 8000}}
    with pytest.raises(ConfigError, match="host"):
        MCPServerConfig.from_dict(data)


# --- TTSEngineConfig.from_dict / from_json / from_json_file ----------------


def test_engine_from_dict_valid():
    cfg = TTSEngineConfig.from_dict(VALID["engine"])
    assert isinstance(cfg, TTSEngineConfig)
    assert cfg.module["type"] == "elevenlabs"
    assert cfg.player.device is None


def test_engine_from_json_and_file_match(tmp_path):
    engine_block = VALID["engine"]
    p = tmp_path / "engine.json"
    p.write_text(json.dumps(engine_block))
    from_dict = TTSEngineConfig.from_dict(engine_block)
    assert TTSEngineConfig.from_json(json.dumps(engine_block)) == from_dict
    assert TTSEngineConfig.from_json_file(str(p)) == from_dict


def test_engine_from_json_file_invalid_json_names_path(tmp_path):
    p = tmp_path / "engine.json"
    p.write_text("{nope")
    with pytest.raises(ConfigError, match=str(p)):
        TTSEngineConfig.from_json_file(str(p))


def test_from_dict_carries_module_verbatim():
    engine_block = {
        "module": {
            "type": "elevenlabs",
            "api_key_env": "ELEVENLABS_API_KEY",
            "voice_id": "abc123",
            "custom_field": "hello",
        },
        "player": {"device": 3},
    }
    cfg = TTSEngineConfig.from_dict(engine_block)
    assert cfg.module == engine_block["module"]
    assert cfg.module["custom_field"] == "hello"
    assert cfg.module["api_key_env"] == "ELEVENLABS_API_KEY"
    assert cfg.player.device == 3


def test_from_dict_player_defaults_when_omitted():
    cfg = TTSEngineConfig.from_dict({"module": VALID["engine"]["module"]})
    assert cfg.player.device is None


def test_from_dict_no_env_read(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    cfg = TTSEngineConfig.from_dict(
        {"module": {"type": "elevenlabs", "api_key_env": "ELEVENLABS_API_KEY"}}
    )
    assert cfg.module["api_key_env"] == "ELEVENLABS_API_KEY"


def test_from_dict_engine_not_object():
    with pytest.raises(ConfigError, match="engine"):
        TTSEngineConfig.from_dict(["not", "an", "object"])  # type: ignore[arg-type]


def test_from_dict_module_missing():
    with pytest.raises(ConfigError, match="engine.module"):
        TTSEngineConfig.from_dict({"player": {"device": None}})


def test_from_dict_module_not_object():
    with pytest.raises(ConfigError, match="engine.module"):
        TTSEngineConfig.from_dict({"module": "elevenlabs"})


def test_from_dict_module_type_empty():
    with pytest.raises(ConfigError, match="engine.module.type"):
        TTSEngineConfig.from_dict({"module": {"type": ""}})


def test_from_dict_module_type_non_string():
    with pytest.raises(ConfigError, match="engine.module.type"):
        TTSEngineConfig.from_dict({"module": {"type": 123}})


def test_from_dict_player_device_bool():
    with pytest.raises(ConfigError, match="device"):
        TTSEngineConfig.from_dict(
            {"module": VALID["engine"]["module"], "player": {"device": True}}
        )


def test_from_dict_player_device_invalid_type():
    with pytest.raises(ConfigError, match="device"):
        TTSEngineConfig.from_dict(
            {"module": VALID["engine"]["module"], "player": {"device": 1.5}}
        )


def test_config_error_is_a_value_error():
    # ConfigError subclasses ValueError so callers composing configs can catch one
    # uniform "bad config" type; a plain `except ValueError` must catch it.
    assert issubclass(ConfigError, ValueError)
    with pytest.raises(ValueError, match="engine.module.type"):
        TTSEngineConfig.from_dict({"module": {}})
