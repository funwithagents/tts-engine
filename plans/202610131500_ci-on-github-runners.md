# CI on GitHub's hosted runners

**Status:** Done

Implements `specs/ci.md` (all sections) and `specs/project.md` ("Platforms", "Key dependencies" — the CPU torch index, "System dependencies"). It adds the CI workflow — the static gate, the fast tier on Linux (x86_64 and arm64), macOS and Windows and the live tier on Linux against a PulseAudio null sink — and makes the Linux torch the CPU build so a runner's sync stays small. It leaves out a live job on macOS or Windows, an Intel macOS entry, setting the provider-key repository secrets, and the required-status-check repository setting (both GitHub settings, outside the repo).

## Scope

- `.github/workflows/ci.yml` — new: `check`, `fast-tier` (4-entry matrix: Linux x86_64 and arm64, macOS, Windows), `e2e` (Linux, null sink, Hugging Face cache, optional key secrets)
- `pyproject.toml`, `uv.lock` — `[[tool.uv.index]]` `pytorch-cpu` + `[tool.uv.sources]` `torch` on Linux; `torch` named in the `dev` group; relocked (the NVIDIA wheels leave the lock)
- `tests/test_config.py`, `tests/modules/test_pocket.py` — surfaced by the first Windows run: a path passed unescaped to `pytest.raises(match=...)`, and a hardcoded POSIX separator; made platform-neutral
- `specs/modules/pocket.md` — the `pocket` extra does not install on Intel macOS (no torch wheel)
- `specs/ci.md` — new spec; `specs/project.md`, `specs/testing.md`, `specs/_index.md` — platforms, CPU index, CI section, closed "CI wiring" open question
- `AGENTS.md` — `.github/workflows/` map row, CI line under Testing, format command scoped to the code dirs

## Steps

1. Write `specs/ci.md` (`Stable`) and update `project.md` (`Updated`), `testing.md`, `_index.md`, `AGENTS.md`.
2. Add the CPU index and the `torch` source to `pyproject.toml`; `uv lock`; confirm no `nvidia-*` package remains and the export installs from wheels on Linux and Windows at Python 3.11.
3. Write `.github/workflows/ci.yml` per `specs/ci.md`, "The workflow" and "The live job's sound device".
4. Push the branch, open the PR, and fix what the macOS, Windows and live entries surface until every job is green.
5. Add `ubuntu-24.04-arm` to the `fast-tier` matrix and state the platform support — Linux arm64 full (`miniaudio` from sdist), Intel macOS partial (no `pocket`), no Intel CI entry — in `ci.md`, `project.md` and `pocket.md`.
6. Mark `ci.md` and `project.md` `Implemented` and this plan `Done`.

## Verification

Locally: `uv run ruff check .`, `uv run ruff format --check src tests tests-e2e`, `uv run pyright`, `uv run pytest`. On the PR: all six jobs green (`check`, `fast-tier` × `ubuntu-24.04`/`ubuntu-24.04-arm`/`macos-15`/`windows-2025`, `e2e`); the `e2e` log's `-rs` summary shows only the skips `specs/ci.md` lists, and every `test_module_say_completes` row for `tone`, `audiofile` and `pocket` passed rather than skipped.
