---
code:
  - .github/workflows/ci.yml
  - pyproject.toml
tests:
---

# Continuous integration

**Status:** Implemented

## Purpose

Both test tiers run on GitHub's hosted runners for every pull request and every push to `main`, so the verification gate of [AGENTS.md](../AGENTS.md) — lint, type check, tests — is a machine's verdict on each change and not only a local command. CI runs exactly what the tiers are designed to run with no credentials ([testing.md](testing.md)): the fast tier whole, on every platform the library supports, and the live tier on Linux against a virtual sound device, every test that cannot meet its needs skipping and the skips read as part of the verdict. Like [testing.md](testing.md) this is a cross-cutting practice, not a runtime concept: nothing here ships in the library, and the one file that implements it is the workflow.

## Decided

### The runners

- **GitHub-hosted runners, pinned images.** `ubuntu-24.04` (x86_64), `ubuntu-24.04-arm` (arm64), `macos-15` (arm64) and `windows-2025` (x86_64), each pinned by name rather than `*-latest`, so the system packages the jobs were validated against move only when a pin is bumped on purpose. No self-hosted runner; the repo is public, so every runner is free.
- **The fast tier on every supported platform — Linux (x86_64 and arm64), macOS, Windows.** The library is pure Python over `sounddevice` and `numpy`, with no platform branch in `src/` ([project.md](project.md), "Platforms"); the fast tier needs no network, no credentials and no sound device ([testing.md](testing.md)), so it runs unchanged on the four runners. Each entry is where that platform's own paths meet a machine of the platform: the lock's wheels (torch from PyPI on macOS and Windows, from the CPU index on Linux — "Dependencies" below; on arm64 Linux `miniaudio`, which ships no arm64 wheel, built from its sdist during the sync), path handling (`resolve_path`, drive letters, CRLF checkouts), and the subprocess guard tests. A failure on one platform is a bug to fix, not a reason to drop the platform. Linux arm64 is an entry because it is where the library lands in a container on Apple Silicon or on an arm server, and the free arm runner makes it one matrix line.
- **No Intel macOS entry.** torch publishes no x86_64 macOS wheel, so the `dev` group (every extra, `pocket` included) cannot sync on an Intel Mac; the partial support stated in [project.md](project.md), "Platforms" — the base and the non-`pocket` extras — runs the same Python the `macos-15` entry tests. Carving `pocket` out of the dev group for a platform at the end of Apple's support is not worth the complexity.
- **The live tier on Linux only.** Of everything the live tier drives, only the default `AudioPlayer` sink differs by platform (PortAudio's host API: ALSA/PulseAudio, Core Audio, WASAPI); the providers, the MCP transport and the `CaptureSink` path behave identically everywhere, and the `pocket` module's `"auto"` device never selects `mps` ([modules/pocket.md](modules/pocket.md)), so macOS takes no torch path of its own. A Linux runner gets a dependable output device from ALSA's built-in null plugin; a hosted macOS or Windows runner offers no such device reliably, so a live job there would mostly prove the runner's audio stack. macOS — the development machine — runs the live tier locally ([AGENTS.md](../AGENTS.md), "Testing"); Windows' live path is untested by CI and stated as such.
- **One Python, 3.11** — the floor of `requires-python`. Local development runs the version `.python-version` names (3.13), so between them both ends of the supported range are exercised. The workflow sets it through `astral-sh/setup-uv`'s `python-version`, which overrides `.python-version` for every `uv` command in the job.

### Dependencies

- **`uv sync --locked`** in every job: the sync fails when `uv.lock` does not match `pyproject.toml`, so a dependency edit lands with its relock or not at all. It installs the `dev` group, which depends on `tts-engine[all]` ([project.md](project.md), "Key dependencies"), so every extra is present and no live row skips for a missing extra.
- **torch from PyTorch's CPU index on Linux.** PyPI's Linux torch wheel is the CUDA build, which drags gigabytes of NVIDIA libraries no runner uses. `pyproject.toml` declares a `[[tool.uv.index]]` named `pytorch-cpu` (`https://download.pytorch.org/whl/cpu`, `explicit = true`) and a `[tool.uv.sources]` entry pinning `torch` to it under `sys_platform == 'linux'`; `torch` is named in the `dev` group although `pocket-tts` brings it, because a uv source applies only to a dependency the project declares itself. macOS and Windows keep PyPI's wheels, CPU-only already. A uv source is not inherited downstream, so this affects the repo's own environment, never a consumer's install of the `pocket` extra.
- **System packages on Linux only, listed in the workflow.** `sounddevice` loads PortAudio at import, which the `sounddevice` wheels bundle on macOS and Windows but not on Linux: the Linux jobs that import it (the fast tier's `AudioPlayer` tests patch `sounddevice.OutputStream`, which imports it for real) apt-install `libportaudio2` ([project.md](project.md), "System dependencies"). The live job installs nothing more: its sound device is ALSA's null plugin, which ships in `libasound2`, a dependency `libportaudio2` already pulls in ("The live job's sound device" below).
- **Caches.** `astral-sh/setup-uv` caches uv's downloads keyed on `uv.lock`; the live job also caches `~/.cache/huggingface` (the pocket-tts, Phonon, Kokoro and Piper weights and voices), keyed on `uv.lock` with a prefix restore key.

### The workflow

One file, `.github/workflows/ci.yml`. It triggers on `pull_request`, on `push` to `main`, and on `workflow_dispatch`. Concurrency is one run per ref: a newer push cancels the older run still in flight.

| Job | Runner | What |
|---|---|---|
| `check` | `ubuntu-24.04` | `uv sync --locked`, then `ruff check .`, `ruff format --check src tests tests-e2e`, `pyright` — the static gate |
| `fast-tier` | matrix: `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `windows-2025` | `libportaudio2` (Linux only), `uv sync --locked`, `uv run pytest` — the fast tier, `fail-fast: false` so one platform's failure leaves the others running |
| `e2e` | `ubuntu-24.04` | `libportaudio2`; ALSA's null plugin as the default PCM; `uv sync --locked`; `uv run pytest tests-e2e -rs --durations=10` |

The six jobs run side by side, each on its own runner, and none waits on another: a run takes as long as its slowest job. Each job carries a `timeout-minutes` well under GitHub's default, so a hung synthesis or server fails in minutes, not hours.

- **The format check covers the code directories only.** `ruff format .` also rewrites the Python blocks inside Markdown files (the plans), so the gate — and the local command in [AGENTS.md](../AGENTS.md) — is `ruff format src tests tests-e2e`.
- **The fast tier runs as locally**: plain `uv run pytest`, whose `testpaths` is `tests/`. The tier carries no platform list and no platform skips.
- **The live tier runs as locally**, serial: `uv run pytest tests-e2e`, the MCP test spawning its own `tts-engine-mcp` subprocess. `-rs` prints every skip into the job log; `--durations=10` prints the ten slowest tests, so a provider that turns slow (the OpenAI-compatible rows, on OpenAI's hosted models, once took 140 s of a 310 s step) shows up in the summary rather than in log timestamps.

### The live job's sound device

On a runner with no sound hardware, `AudioPlayer` would find no output device and `test_module_say_completes` would fail for every row. Before the tests the job writes `~/.asoundrc` with `pcm.!default { type null }` and `ctl.!default { type null }`: ALSA's built-in null plugin, part of `libasound2`, becomes the `default` PCM, which is the device PortAudio opens for `player.device: null`. PortAudio lists it as `default` and opens output streams at any rate on it (checked on a clean `ubuntu-24.04` image with only `libportaudio2` installed, at 16, 24 and 44.1 kHz). Every `test_module_say_completes` row then opens a real output stream at its module's declared sample rate and plays into a device that discards it. The live tests assert only that the pass completes ([testing.md](testing.md)), so silence is a valid signal.

The job used to run a PulseAudio null sink behind ALSA's `pulse` plugin. That needed `pulseaudio` and `libasound2-plugins`, 56 packages and 34 MB once their dependencies came in (`libasound2-plugins` pulls `libavcodec`, which pulls the video codecs), and GitHub's Ubuntu mirror is intermittently throttled to under 100 kB/s: the install step took 8 to 16 minutes in three of four consecutive runs against 18 s when the mirror was healthy. The null plugin needs no package and no daemon. The one behavioural difference is pacing: PulseAudio's null sink consumed audio in real time, so each `test_module_say_completes` row took as long as its sentence; the ALSA null plugin returns from a write at once, so those rows no longer wait on playback. Nothing in the tier asserts on playback timing — the cancellation tests pace their own capture sink ([testing.md](testing.md)) — so the device's clock is not load-bearing.

### Secrets and expected skips

- **No secret is required.** Every job is green with none. The ElevenLabs, Gradium, Mistral, Deepgram, Gemini, Cartesia and OpenAI keys are optional repository secrets, mapped onto `ELEVENLABS_API_KEY` / `GRADIUM_API_KEY` / `MISTRAL_API_KEY` / `DEEPGRAM_API_KEY` / `GEMINI_API_KEY` / `CARTESIA_API_KEY` / `OPENAI_API_KEY` for the live step only; set, those providers' rows run; absent — or on a fork's pull request, which never receives a secret — the variables are empty and `require_module` skips the rows ([testing.md](testing.md), "Live tier: skip without credentials").
- **Expected skips.** A skip is not a pass. In the `fast-tier` jobs nothing skips. In the `e2e` job the following skip by design, and any other skip in the job log is an environment regression to investigate:

| Skips in `e2e` | Why |
|---|---|
| every `test_modules.py` scenario's `[elevenlabs]` row | only when the `ELEVENLABS_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[gradium]` row | only when the `GRADIUM_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[mistral]` row | only when the `MISTRAL_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[deepgram]` and `[deepgram-flux]` rows | only when the `DEEPGRAM_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[gemini]` row | when the `GEMINI_API_KEY` secret is absent (or on a fork's pull request); and, per test, when the key's quota is exhausted (`429 RESOURCE_EXHAUSTED`, [testing.md](testing.md), "Live tier: skip without credentials") |
| every `test_modules.py` scenario's `[cartesia]` row | only when the `CARTESIA_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[openai]` and `[openai-compatible]` rows | only when the `OPENAI_API_KEY` secret is absent (or on a fork's pull request) |
| every `test_modules.py` scenario's `[qwen3]` row | always: the `qwen3` extra installs only on Apple-silicon macOS, so `qwentts_cpp` is absent on the Linux runner ([modules/qwen3.md](modules/qwen3.md)) |

`pocket`, `phonon`, `kokoro`, `piper`, `tone`, `audiofile` and the MCP test never skip in CI: their extras come with the `dev` group and they need no key.

- **Required status checks on `main`** — `check`, the four `fast-tier` entries and `e2e` — are a repository setting on GitHub, outside the repo.

## Relationship to the other specs

- **[testing.md](testing.md):** the two tiers, the skip-without-credentials gate and the robust-property assertions CI runs on. CI is the hosted run of that strategy, with no rule of its own about what a test does.
- **[project.md](project.md):** the supported platforms, the `dev` group CI installs whole, the PyTorch CPU index, `libportaudio2`, `.github/` in the repo shape.
- **[AGENTS.md](../AGENTS.md):** the verification gate CI automates.

## Open questions

1. **A live job on macOS or Windows.** Deferred: it would exercise `AudioPlayer` on Core Audio or WASAPI, but only behind a virtual device the hosted runners do not provide reliably. Revisit if a platform-specific playback bug ever slips past local runs.
