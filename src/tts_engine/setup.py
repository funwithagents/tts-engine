"""Setting the engine up from an application, as data (specs/setup.md).

Seven functions over the engine block (the document `TTSEngineConfig.from_dict`
takes): what its config looks like, which extra a config needs, whether the
engine would accept it, what it needs on the machine, which voices and devices
it can choose from, and one real run to hear it work. They implement the module
contract of the `module-setup` project and exchange JSON-compatible data only.

`describe`, `requirements` and `validate` run on the base install: no extra, no
key, no network. Nothing here is imported by the engine itself.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

from tts_engine.audio import AudioSink
from tts_engine.config import (
    GAIN_DB_MAX,
    GAIN_DB_MIN,
    ConfigError,
    TTSEngineConfig,
    _check_gain_db,
    _check_module,
    _check_player,
)
from tts_engine.modules import REGISTRY, RENAMED
from tts_engine.modules.base import TTSModule
from tts_engine.modules.fields import fields_schema

__all__ = [
    "PROBE_SENTENCE",
    "check",
    "describe",
    "options",
    "prepare",
    "probe",
    "requirements",
    "validate",
]

_DISTRIBUTION = "tts-engine"
PROBE_SENTENCE = "Hello, this is a test of the voice you selected."


def _module_class(config: Any) -> type[TTSModule] | None:
    """The class of the module the engine block selects, or None when it
    selects none that is registered. A renamed type resolves quietly."""
    module = config.get("module") if isinstance(config, dict) else None
    module_type = module.get("type") if isinstance(module, dict) else None
    if not isinstance(module_type, str):
        return None
    return REGISTRY.get(RENAMED.get(module_type, module_type))


def _declared(cls: type[TTSModule]) -> bool:
    """Whether the module declares its fields yet (specs/setup.md); the ones
    that do not are left out of the schema and of field validation."""
    return hasattr(cls, "fields")


def describe() -> dict[str, Any]:
    branches = []
    for name, cls in REGISTRY.items():
        if not _declared(cls):
            continue
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
                    "title": "Voice",
                    "description": "The backend that turns text into speech.",
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


def requirements(config: dict[str, Any]) -> list[str]:
    cls = _module_class(config)
    extra = getattr(cls, "extra", None) if cls is not None else None
    return [f"{_DISTRIBUTION}[{extra}]"] if extra else []


def _problem(path: str, error: Exception) -> dict[str, Any]:
    return {"path": path, "message": str(error)}


def validate(config: dict[str, Any]) -> list[dict[str, Any]]:
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
        elif _declared(cls):
            problems += [
                {"path": f"/module/{name}" if name else "/module", "message": message}
                for name, message in cls.problems(module)
            ]
    return problems


def _selected(config: dict[str, Any]) -> tuple[type[TTSModule], dict[str, Any]]:
    """The module class and block of a config `validate` accepts."""
    cls = _module_class(config)
    if cls is None:
        raise ConfigError("the config selects no registered TTS module")
    return cls, config["module"]


def check(config: dict[str, Any]) -> list[dict[str, Any]]:
    cls, module = _selected(config)
    return cls.items(module) if _declared(cls) else []


def prepare(
    config: dict[str, Any],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    cls, module = _selected(config)
    if _declared(cls):
        cls.prepare(module, on_progress)


def _voice_label(voice: Any) -> str:
    details = [", ".join(voice.languages)] if voice.languages else []
    if voice.gender:
        details.append(voice.gender)
    return voice.name + (f" ({', '.join(details)})" if details else "")


def options(name: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    if name == "voices":
        cls, module = _selected(config)
        return [
            {"label": _voice_label(voice), "fields": dict(voice.config)}
            for voice in cls.list_voices(module)
        ]
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
    return []


def _speak(config: dict[str, Any], sink: AudioSink | None) -> str:
    """Build the engine, say the probe sentence, close; what was done, in words."""
    from tts_engine.engine import TTSEngine

    engine_config = TTSEngineConfig.from_dict(config)

    async def run() -> None:
        engine = TTSEngine(engine_config, sink=sink)
        try:
            await engine.say(PROBE_SENTENCE)
        finally:
            await engine.close()

    asyncio.run(run())
    if sink is not None or engine_config.player.discard:
        return "Synthesized a test sentence; nothing was played on a device."
    device = engine_config.player.device
    where = "the default device" if device is None else f"device {device!r}"
    return f"Spoke a test sentence on {where}."


def probe(config: dict[str, Any], *, sink: AudioSink | None = None) -> dict[str, Any]:
    """Speak one test sentence with this config and report how it went. Never
    raises. `sink` replaces the device (for a caller with its own audio path,
    and for tests)."""
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["message"] = _speak(config, sink)
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
            outcome["message"] = str(exc) or type(exc).__name__
            outcome["ok"] = False

    started = time.monotonic()
    # Its own thread: `asyncio.run` needs one without a running loop, and the
    # caller may be on a loop's thread.
    worker = threading.Thread(target=work, name="tts-engine-probe")
    worker.start()
    worker.join()
    return {
        "ok": outcome["ok"],
        "message": outcome["message"],
        "duration_s": round(time.monotonic() - started, 3),
    }
