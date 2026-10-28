"""The provider extras and their bundles stay in step with the module registry.

See specs/project.md, "Dependency strategy for TTS backends".
"""

import re
import tomllib
from pathlib import Path

from tts_engine.modules import REGISTRY

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
_SELF = re.compile(r"^tts-engine\[([^\]]+)\]$")
_TRANSPORTS = {"mcp"}
_BUNDLES = {"hosted-modules", "all-modules", "all"}


def _extras() -> dict[str, list[str]]:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]


def _providers(kind: str | None = None) -> set[str]:
    return {
        name
        for name, module in REGISTRY.items()
        if module.kind != "fixture" and (kind is None or module.kind == kind)
    }


def _expand(extras: dict[str, list[str]], name: str) -> set[str]:
    """The non-bundle extras ``name`` installs, following self-references."""
    if name not in _BUNDLES:
        return {name}
    found: set[str] = set()
    for req in extras[name]:
        match = _SELF.match(req)
        assert match, f"the {name!r} bundle lists only tts-engine extras: {req!r}"
        for member in match.group(1).split(","):
            found |= _expand(extras, member.strip())
    return found


def test_every_provider_has_an_extra_named_after_its_type() -> None:
    extras = set(_extras())
    assert extras - _BUNDLES - _TRANSPORTS == _providers()


def test_hosted_modules_bundles_exactly_the_hosted_providers() -> None:
    assert _expand(_extras(), "hosted-modules") == _providers("hosted")


def test_all_modules_bundles_every_provider_and_no_transport() -> None:
    assert _expand(_extras(), "all-modules") == _providers()


def test_all_bundles_every_extra() -> None:
    extras = _extras()
    assert _expand(extras, "all") == set(extras) - _BUNDLES
