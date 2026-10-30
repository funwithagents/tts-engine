"""Documentation drift guards: relative links resolve, the module tables match
the registry, and each backend page lists its spec's fields (specs/project.md,
"Documentation checks")."""

import re
from pathlib import Path

import pytest

from tts_engine import VoiceListingUnsupported
from tts_engine.modules import REGISTRY

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The Markdown a reader can reach from the repository: the root files and the
# tracked documentation trees. Left out: `plans/` (records of what was built,
# not rewritten when a file they link to later moves), `analysis/` (untracked
# notes) and the benchmark's git-ignored `runs/`.
_ROOT_FILES = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
_DOC_DIRS = [
    "docs",
    "specs",
    "benchmarks",
    "examples",
    "tests",
    "tests-e2e",
]
_IGNORED_PARTS = {"runs", "__pycache__"}


def _markdown_files() -> list[Path]:
    files = [_REPO_ROOT / name for name in _ROOT_FILES if (_REPO_ROOT / name).exists()]
    for directory in _DOC_DIRS:
        for path in sorted((_REPO_ROOT / directory).rglob("*.md")):
            if not _IGNORED_PARTS & set(path.relative_to(_REPO_ROOT).parts):
                files.append(path)
    return files


# --- Relative links -----------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _prose(text: str) -> str:
    """The text with code blocks and code spans removed: a link inside code is
    an example, not a link."""
    return _CODE_SPAN.sub("", _FENCE.sub("", text))


def _slug(heading: str) -> str:
    """GitHub's heading anchor: markup dropped, lower-cased, punctuation
    removed, spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for heading in _HEADING.findall(_FENCE.sub("", path.read_text(encoding="utf-8"))):
        slug = _slug(heading)
        candidate, n = slug, 0
        while candidate in anchors:  # GitHub numbers repeated headings
            n += 1
            candidate = f"{slug}-{n}"
        anchors.add(candidate)
    return anchors


def _broken_links(path: Path) -> list[str]:
    broken: list[str] = []
    for target in _LINK.findall(_prose(path.read_text(encoding="utf-8"))):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            broken.append(target)
        elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
            broken.append(f"{target} (no such heading)")
    return broken


@pytest.mark.parametrize(
    "path", _markdown_files(), ids=lambda p: str(p.relative_to(_REPO_ROOT))
)
def test_relative_links_resolve(path: Path):
    assert not _broken_links(path), f"broken links in {path.relative_to(_REPO_ROOT)}"


# --- The module tables -------------------------------------------------------

# The full table in the module index, and the README's short copy. Both name
# each module as `type`, plain or linked to its page.
_MODULE_INDEX = _REPO_ROOT / "docs" / "modules" / "index.md"
_MODULE_TABLES = [_MODULE_INDEX, _REPO_ROOT / "README.md"]
_HEADER = re.compile(r"^\| Module \| `kind` \|")
_NAME = re.compile(r"`([^`]+)`")


def _table(path: Path, header: re.Pattern[str]) -> list[dict[str, str]]:
    """The first table of `path` whose header row matches, as one dict per row."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if header.match(line))
    columns = [cell.strip().strip("`") for cell in lines[start].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(dict(zip(columns, cells, strict=True)))
    return rows


def _module_rows(path: Path) -> dict[str, dict[str, str]]:
    """The module table of `path` as {type: {column: cell}}."""
    rows: dict[str, dict[str, str]] = {}
    for row in _table(path, _HEADER):
        match = _NAME.search(row["Module"])
        assert match, f"no `type` in the Module cell {row['Module']!r} of {path.name}"
        rows[match.group(1)] = {key: value.strip("`") for key, value in row.items()}
    return rows


@pytest.mark.parametrize(
    "path", _MODULE_TABLES, ids=lambda p: str(p.relative_to(_REPO_ROOT))
)
def test_module_table_lists_every_registered_module_with_its_kind(path: Path):
    rows = _module_rows(path)
    assert {name: row["kind"] for name, row in rows.items()} == {
        name: cls.kind for name, cls in REGISTRY.items()
    }


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_module_table_voice_listing_cell(name: str):
    # Only the cells that need no network are checked by calling: a `no`
    # backend refuses before reading anything, a fixture lists nothing.
    # A `yes` backend would reach its provider or download a catalog.
    cell = _module_rows(_MODULE_INDEX)[name]["Voice listing"]
    cls = REGISTRY[name]
    assert cell in {"yes", "no", "—"}
    if cell == "no":
        with pytest.raises(VoiceListingUnsupported):
            cls.list_voices({"type": name})
    elif cell == "—":
        assert cls.kind == "fixture"
        assert cls.list_voices({"type": name}) == []
    else:
        assert cls.kind != "fixture"


# --- Backend pages against their specs ----------------------------------------

# A backend page's option table is the user's copy of its spec's table: the
# same fields, in user words. `base_dir` is the loaders' reserved key, which
# every module accepts and the specs list; the pages leave it to
# docs/configuration.md.
_FIELD_HEADER = re.compile(r"^\| Field \|")
_RESERVED_FIELDS = {"base_dir"}


def _fields(path: Path) -> set[str]:
    return {
        match.group(1)
        for row in _table(path, _FIELD_HEADER)
        if (match := _NAME.search(row["Field"]))
    }


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_backend_page_lists_the_fields_of_its_spec(name: str):
    page = _REPO_ROOT / "docs" / "modules" / f"{name}.md"
    spec = _REPO_ROOT / "specs" / "modules" / f"{name}.md"
    assert page.exists(), f"no page for the {name!r} module: add docs/modules/{name}.md"
    assert _fields(page) == _fields(spec) - _RESERVED_FIELDS
