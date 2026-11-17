import ast
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_PKG = _REPO_ROOT / "src" / "tts_engine"
_SPECS_DIR = _REPO_ROOT / "specs"

# Matches link targets like `src/tts_engine/foo.py` anywhere in AGENTS.md.
# Only top-level package modules match (the anchor is `src/tts_engine/` immediately
# followed by a filename), so `src/tts_engine/modules/base.py` is intentionally not
# captured — the map covers the top-level concept modules.
_MODULE_LINK = re.compile(r"src/tts_engine/([A-Za-z_][A-Za-z0-9_]*\.py)")

# Package glue that isn't a spec'd concept, so it needs no owning spec and no map row.
_NON_CONCEPT_MODULES = {"__init__.py"}

# Concept specs that legitimately govern no single code module (project-wide
# overviews). Exempt from the "must declare code" invariant only.
_NON_CODE_SPECS = {"overview.md"}


def _mapped_modules() -> set[str]:
    text = _AGENTS_MD.read_text(encoding="utf-8")
    return set(_MODULE_LINK.findall(text))


def _actual_modules() -> set[str]:
    return {path.name for path in _PKG.glob("*.py")}


def _concept_modules() -> set[str]:
    return _actual_modules() - _NON_CONCEPT_MODULES


def test_agents_md_maps_exactly_the_package_modules():
    mapped = _mapped_modules()
    concept = _concept_modules()

    missing_from_map = concept - mapped
    stale_in_map = mapped - concept

    assert not missing_from_map, (
        f"src package modules not listed in the AGENTS.md project map: {sorted(missing_from_map)}. "
        "Add a row for each new module."
    )
    assert not stale_in_map, (
        f"AGENTS.md project map references modules that no longer exist: {sorted(stale_in_map)}. "
        "Remove or rename their rows."
    )


# --- Spec frontmatter drift guards ------------------------------------------
#
# Every concept spec declares the code/tests it governs in a YAML frontmatter
# block:
#
#     ---
#     code:
#       - src/tts_engine/example.py
#     tests:
#       - tests/test_example.py
#     ---
#
# The spec-drift checks read this to scope what they diff, so it must stay
# honest: listed paths must exist, and every concept module must be governed by
# at least one spec. The mapping is many-to-many.
#
# Parsed with a tiny hand-rolled reader (not PyYAML) — the frontmatter format is
# authored in this repo and simple.

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_LIST_KEY = re.compile(r"^(code|tests):\s*$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.+?)\s*$")


def _spec_files() -> list[Path]:
    # Concept specs only — skip index/template/scratch files (`_index.md`, `_spec-template.md`, ...).
    # Recursive: per-backend module specs live in `specs/modules/`.
    return sorted(p for p in _SPECS_DIR.rglob("*.md") if not p.name.startswith("_"))


def _parse_frontmatter(path: Path) -> dict[str, list[str]]:
    """Return {'code': [...], 'tests': [...]} from a spec's frontmatter block."""
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        return {}
    result: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key_match = _LIST_KEY.match(line)
        if key_match:
            current = result.setdefault(key_match.group(1), [])
            continue
        item_match = _LIST_ITEM.match(line)
        if item_match and current is not None:
            current.append(item_match.group(1))
    return result


def test_every_spec_declares_the_code_it_governs():
    missing: list[str] = []
    for spec in _spec_files():
        if spec.name in _NON_CODE_SPECS:
            continue
        front = _parse_frontmatter(spec)
        if not front.get("code"):
            missing.append(spec.name)
    assert not missing, (
        f"specs missing a non-empty `code:` frontmatter list: {missing}. "
        "Add a frontmatter block naming the files this spec governs so the "
        "spec-drift checks know what to check."
    )


def test_spec_frontmatter_paths_all_exist():
    stale: list[str] = []
    for spec in _spec_files():
        front = _parse_frontmatter(spec)
        for rel in front.get("code", []) + front.get("tests", []):
            if not (_REPO_ROOT / rel).exists():
                stale.append(f"{spec.name} -> {rel}")
    assert not stale, (
        f"spec frontmatter points at paths that no longer exist: {sorted(stale)}. "
        "Update the `code:`/`tests:` lists when files are renamed or removed."
    )


def test_every_concept_module_is_governed_by_a_spec():
    governed: set[str] = set()
    for spec in _spec_files():
        for rel in _parse_frontmatter(spec).get("code", []):
            if rel.startswith("src/tts_engine/"):
                governed.add(Path(rel).name)

    ungoverned = _concept_modules() - governed
    assert not ungoverned, (
        f"src package modules not named in any spec's `code:` frontmatter: {sorted(ungoverned)}. "
        "Add each to the frontmatter of the spec that governs it "
        f"(package glue exempt from this rule: {sorted(_NON_CONCEPT_MODULES)})."
    )


# --- statuses and module coverage ------------------------------------------------

_STATUS_LINE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
_INDEX_ROW = re.compile(r"^\| \[[^\]]+\]\(([^)]+)\) \|.*\| ([^|]+?) \|$", re.MULTILINE)


def _status_mismatches(folder: Path, files: list[Path]) -> list[str]:
    """Files whose `**Status:**` line differs from their row in the folder's
    `_index.md`, or that have no row (or no status line) at all."""
    index = (folder / "_index.md").read_text(encoding="utf-8")
    rows = {target: status for target, status in _INDEX_ROW.findall(index)}
    mismatches = []
    for path in files:
        rel = path.relative_to(folder).as_posix()
        own = _STATUS_LINE.search(path.read_text(encoding="utf-8"))
        if own is None or rows.get(rel) != own.group(1):
            mismatches.append(
                f"{rel}: file says {own.group(1) if own else None!r}, "
                f"index says {rows.get(rel)!r}"
            )
    return mismatches


def test_spec_statuses_match_the_index():
    mismatches = _status_mismatches(_SPECS_DIR, _spec_files())
    assert not mismatches, (
        f"spec statuses out of step with specs/_index.md: {mismatches}. "
        "Update the spec's `**Status:**` line and its index row together."
    )


def test_plan_statuses_match_the_index():
    plans = _REPO_ROOT / "plans"
    files = sorted(p for p in plans.glob("*.md") if not p.name.startswith("_"))
    mismatches = _status_mismatches(plans, files)
    assert not mismatches, (
        f"plan statuses out of step with plans/_index.md: {mismatches}. "
        "Update the plan's `**Status:**` line and its index row together."
    )


def _live_matrix_modules() -> set[str]:
    """The module types of the `MODULES` table in tests-e2e/support.py, read
    from its source: the live tier is not importable from the unit tier."""
    tree = ast.parse((_REPO_ROOT / "tests-e2e" / "support.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MODULES" for t in node.targets
        ):
            return {
                call.args[0].value
                for call in ast.walk(node.value)
                if isinstance(call, ast.Call)
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            }
    raise AssertionError("no MODULES table in tests-e2e/support.py")


def test_every_registered_module_has_a_live_row_and_a_benchmark_entry():
    from tts_engine.modules import REGISTRY

    matrix = json.loads((_REPO_ROOT / "benchmarks" / "matrix.json").read_text("utf-8"))
    benchmarked = {entry["type"] for entry in matrix["entries"]}
    assert set(REGISTRY) - _live_matrix_modules() == set(), (
        "registered modules with no row in tests-e2e/support.py MODULES"
    )
    assert set(REGISTRY) - benchmarked == set(), (
        "registered modules with no entry in benchmarks/matrix.json"
    )
