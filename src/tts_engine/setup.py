"""Setting the engine up from an application, as data (specs/setup.md).

Seven functions over the engine block (the document `TTSEngineConfig.from_dict`
takes): what its config looks like (`config_schema`), which extra a config needs
(`required_extras`), what is wrong in the config itself (`check_config`), what
this machine is missing to run it (`check_setup`: the extra, the keys, the
files), downloading one file (`prepare`), the choices of a field (`options`),
and one real run to hear it work (`probe`). They implement the module contract
of the `app-setup` project and exchange JSON-compatible data only.

Each is the engine's own part (the `player` and `gain_db` blocks, the module
choice) plus the method of the same name on the selected module. All but
`prepare`, `options` and `probe` run on the base install, with no network.
Nothing here is imported by the engine itself.
"""

import threading
import time
from collections.abc import Callable
from typing import Any

from tts_engine.config import (
    GAIN_DB_MAX,
    GAIN_DB_MIN,
    ConfigError,
    TTSEngineConfig,
    _check_gain_db,
    _check_module,
    _check_player,
)
from tts_engine.modules import REGISTRY, RENAMED, load_module, resolve_type
from tts_engine.modules.base import TTSModule, VoiceListingUnsupported
from tts_engine.modules.fields import fields_schema

__all__ = [
    "check_config",
    "check_setup",
    "config_schema",
    "options",
    "prepare",
    "probe",
    "required_extras",
]

_DISTRIBUTION = "tts-engine"


def _module_class(config: Any) -> type[TTSModule] | None:
    """The class of the module the engine block selects, or None when it
    selects none that is registered. A renamed type resolves quietly."""
    module = config.get("module") if isinstance(config, dict) else None
    module_type = module.get("type") if isinstance(module, dict) else None
    if not isinstance(module_type, str):
        return None
    return REGISTRY.get(RENAMED.get(module_type, module_type))


def config_schema() -> dict[str, Any]:
    branches = []
    for name, cls in REGISTRY.items():
        schema = fields_schema(cls.fields)
        branches.append(
            {
                "title": f"{cls.title} ({cls.kind})",
                "x-options": "voices",
                "required": schema["required"],
                "properties": {"type": {"const": name}, **schema["properties"]},
            }
        )
    return {
        "contract": 1,
        "distribution": _DISTRIBUTION,
        "title": "Text to speech",
        "schema": {
            "type": "object",
            "required": ["module"],
            "properties": {
                "module": {
                    "type": "object",
                    "title": "Module",
                    "description": "What turns text into speech: a provider's API "
                    "(hosted) or a model run on this machine (local).",
                    "required": ["type"],
                    "oneOf": branches,
                },
                "player": {
                    "type": "object",
                    "title": "Output",
                    "description": "Where the speech is played.",
                    "properties": {
                        "device": {
                            "type": ["string", "integer", "null"],
                            "description": "The output device, by name or by "
                            "index. Absent, the system's default.",
                            "x-options": "output-devices",
                        },
                        "discard": {
                            "type": "boolean",
                            "description": "Synthesize without playing on any device.",
                            "default": False,
                            "x-advanced": True,
                        },
                    },
                },
                "gain_db": {
                    "type": "number",
                    "description": "A gain applied to the speech before it is "
                    "played, in dB.",
                    "default": 0,
                    "minimum": GAIN_DB_MIN,
                    "maximum": GAIN_DB_MAX,
                    "x-advanced": True,
                },
            },
        },
    }


def required_extras(config: dict[str, Any]) -> list[str]:
    cls = _module_class(config)
    if cls is None:
        return []
    module = config["module"]
    return cls.required_extras(module)


def _problem(path: str, error: Exception) -> dict[str, Any]:
    return {"path": path, "message": str(error)}


def check_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        return [{"path": "", "message": "'engine' must be an object"}]
    problems: list[dict[str, Any]] = []

    module = config.get("module")
    module_ok = False
    try:
        _check_module(module)
        module_ok = True
    except ConfigError as exc:
        if not isinstance(module, dict):
            path = "/module"
        elif not isinstance(module.get("type"), str) or not module.get("type"):
            path = "/module/type"
        else:
            path = "/module/base_dir"
        problems.append(_problem(path, exc))

    player = config.get("player", {})
    if not isinstance(player, dict):
        problems.append(
            {"path": "/player", "message": "'engine.player' must be an object"}
        )
    else:
        device, discard = player.get("device"), player.get("discard", False)
        try:
            _check_player(device, discard)
        except ConfigError as exc:
            device_ok = device is None or (
                not isinstance(device, bool) and isinstance(device, str | int)
            )
            problems.append(
                _problem("/player/discard" if device_ok else "/player/device", exc)
            )

    if "gain_db" in config:
        try:
            _check_gain_db(config["gain_db"])
        except ConfigError as exc:
            problems.append(_problem("/gain_db", exc))

    if module_ok:
        assert isinstance(module, dict)
        cls = _module_class(config)
        if cls is None:
            problems.append(
                {
                    "path": "/module/type",
                    "message": f"Unknown TTS module type: {module['type']!r}",
                }
            )
        else:
            problems += [
                {"path": f"/module/{name}" if name else "/module", "message": message}
                for name, message in cls.check_config(module)
            ]
    return problems


def _selected(
    config: dict[str, Any], base_dir: str | None
) -> tuple[type[TTSModule], dict[str, Any]]:
    """The module class and block of a config `check_config` accepts, the block as the
    loader gives it to the module: its relative paths against *base_dir*, the folder the
    app also builds its engine against (the working directory when None)."""
    cls = _module_class(config)
    if cls is None:
        raise ConfigError("the config selects no registered TTS module")
    return cls, TTSEngineConfig.from_dict(config, base_dir=base_dir).module


def check_setup(
    config: dict[str, Any], *, base_dir: str | None = None
) -> list[dict[str, Any]]:
    cls, module = _selected(config, base_dir)
    return cls.check_setup(module)


def prepare(
    config: dict[str, Any],
    item: str,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    base_dir: str | None = None,
) -> None:
    """Make one preparable item of this config ready (an id `check_setup` returned),
    on the calling thread; another item may be prepared at the same time on another.
    `on_progress` receives `{"fraction", "message"}` about this item."""
    cls, module = _selected(config, base_dir)
    cls.prepare(module, item, on_progress)


def options(
    name: str, config: dict[str, Any], *, base_dir: str | None = None
) -> list[dict[str, Any]]:
    if name == "output-devices":
        import sounddevice as sd

        found: list[dict[str, Any]] = [
            {"label": "System default", "fields": {"device": None}}
        ]
        for device in sd.query_devices():
            if device["max_output_channels"] > 0:
                found.append(
                    {"label": device["name"], "fields": {"device": device["name"]}}
                )
        return found
    cls, module = _selected(config, base_dir)
    return cls.options(name, module)


def _check(config: dict[str, Any], base_dir: str | None) -> str:
    """The lightest real check of this config, what it showed in words; raises what
    goes wrong. A hosted module makes one authenticated, free call (its voice list)
    and looks for the configured voice there; one with no voice list is built and
    closed; a local module loads its model and releases it. Nothing is synthesized
    and no device is opened."""
    block = TTSEngineConfig.from_dict(config, base_dir=base_dir).module
    cls = REGISTRY[resolve_type(block.get("type"))]
    problems = cls.check_config(block)
    if problems:
        raise ConfigError(problems[0][1])
    if cls.kind == "hosted":
        try:
            voices = cls.list_voices(block)
        except VoiceListingUnsupported:
            _build_and_close(block)
            return (
                f"{cls.title} has no voice list to check the key with: the module "
                "was built, the key is checked at the first utterance."
            )
        chosen = [
            voice
            for voice in voices
            if voice.config
            and all(block.get(name) == value for name, value in voice.config.items())
        ]
        named = any(name in block for voice in voices for name in voice.config)
        if named and not chosen:
            raise ConfigError(
                f"Authenticated, but the configured voice is not among the "
                f"{len(voices)} voices {cls.title} lists for this account."
            )
        found = (
            f"; the configured voice is one of them ({chosen[0].name})"
            if chosen
            else ""
        )
        return f"Authenticated: {len(voices)} voices listed{found}."
    _build_and_close(block)
    if cls.kind == "local":
        return "Loaded the model and released it."
    return "Built the module."


def _build_and_close(block: dict[str, Any]) -> None:
    module = load_module(block)
    module.close()


def probe(config: dict[str, Any], *, base_dir: str | None = None) -> dict[str, Any]:
    """Check this config for real, as lightly as possible (`_check`): never a
    synthesis, never a device. Never raises. Relative paths against *base_dir*."""
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["message"] = _check(config, base_dir)
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
            outcome["message"] = str(exc) or type(exc).__name__
            outcome["ok"] = False

    started = time.monotonic()
    # Its own thread: some modules run an event loop of their own while they list
    # or build, and the caller may be on a loop's thread.
    worker = threading.Thread(target=work, name="tts-engine-probe")
    worker.start()
    worker.join()
    return {
        "ok": outcome["ok"],
        "message": outcome["message"],
        "duration_s": round(time.monotonic() - started, 3),
    }
