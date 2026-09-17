# Fixture modules, `base_dir`, and provider extras (no default provider)

**Status:** Done

Implements the settled design in `specs/tone-module.md`, `specs/audiofile-module.md`, `specs/configuration.md` ("Constructors", "`engine.module` block"), `specs/tts-module-interface.md` ("Module registry", "Module kinds", "Path resolution", "Dependencies"), `specs/project.md` ("Key dependencies", "Dependency strategy for TTS backends"), `specs/testing.md` ("Default module vs per-module configs", "`MODULES` rows", skip gate), `specs/elevenlabs-module.md` ("Dependencies and lazy import"), and `specs/pocket-module.md` (`voice` path resolution).

It delivers, in this order: (A) the `base_dir` plumbing in the config loaders plus the shared `resolve_path` helper; (B) the two fixture modules `tone` and `audiofile`; (C) ElevenLabs moved behind an `elevenlabs` extra, the base install made provider-agnostic, and every "ElevenLabs is the default" trace removed from code, tests, examples, and docs. It deliberately leaves out any new provider, any change to the module ABC, and any change to the engine, tools, or MCP layers.

> **How to work this plan.** The parts are ordered so the repo is green (lint, type check, unit tests) at the end of each part — do them in order and run the **Checkpoint** commands before moving on. Every step names the exact file and says what to write; the design is fully decided in the specs linked above, so do not re-decide it. If a step's instruction and a spec disagree, the spec wins — follow the spec and note the discrepancy in your final report. Do not edit `specs/` except for the status lines named in Part D.

## Scope

Files touched, grouped by part.

**Part A — `base_dir`**
- `src/tts_engine/config.py` — `base_dir` keyword on `from_dict`/`from_json` of both configs; `from_json_file` derives it; `module.base_dir` rules.
- `src/tts_engine/modules/base.py` — new `resolve_path(config, value)` helper.
- `tests/test_config.py` — tests for the `base_dir` rules.
- `tests/modules/test_tts_module_interface.py` — tests for `resolve_path`.

**Part B — fixture modules**
- `src/tts_engine/modules/tone.py` — **new** `ToneModule`.
- `src/tts_engine/modules/audiofile.py` — **new** `AudioFileModule`.
- `src/tts_engine/modules/__init__.py` — register both.
- `src/tts_engine/modules/pocket.py` — resolve a `.wav` `voice` through `resolve_path`.
- `tests/modules/test_tone.py`, `tests/modules/test_audiofile.py` — **new** unit tests.
- `tests/modules/test_pocket.py` — one test for the `.wav` voice path.
- `tests-e2e/fixtures/hello.wav`, `tests-e2e/fixtures/default.wav` — **new** committed WAV fixtures.
- `tests-e2e/support.py` — `tone` becomes the default module; `tone` and `audiofile` rows in `MODULES`.

**Part C — ElevenLabs as an extra, no default provider**
- `pyproject.toml` — move `elevenlabs` + `miniaudio` to an `elevenlabs` extra; add `all`; add both to the `dev` group.
- `src/tts_engine/modules/elevenlabs.py` — lazy imports; `_ChunkSource` built by a factory.
- `tests/modules/test_lazy_imports.py` — **new** subprocess guard: the package imports with every provider library poisoned.
- `tests/modules/test_elevenlabs.py` — adapt the SDK patch target to the lazy import.
- `tests/test_mcp_server_cli.py` — config uses `tone` instead of `elevenlabs`.
- `tests-e2e/support.py` — `_REQUIRED_IMPORT["elevenlabs"] = "elevenlabs"`.
- `examples/config.elevenlabs.json`, `examples/config.pocket.json`, `examples/config.tone.json`, `examples/config.audiofile.json` — **new**; `config.example.json` — **deleted**.

**Part D — docs and statuses**
- `AGENTS.md`, `README.md` — remove every "default"/"base install" claim about ElevenLabs; document extras, fixture modules, `examples/`, `base_dir`.
- `specs/*.md` status lines + `specs/_index.md`, `plans/_index.md` — status upkeep.

## Steps

### Part A — `base_dir` in the config loaders + `resolve_path`

**A1. Add `resolve_path` to `src/tts_engine/modules/base.py`.**
Add `from pathlib import Path` to the imports and this function at the end of the file:

```python
def resolve_path(config: dict, value: str) -> Path:
    """Resolve a file path from a module config field.

    Absolute paths are returned as-is. A relative path is joined onto
    ``config["base_dir"]`` when present, else onto the current working
    directory. This is a locate only: the file is not opened or checked.
    """
    path = Path(value)
    if path.is_absolute():
        return path
    base_dir = config.get("base_dir")
    if base_dir:
        return Path(base_dir) / path
    return Path.cwd() / path
```

**A2. Thread `base_dir` through `src/tts_engine/config.py`.**
Add `from pathlib import Path` to the imports. Then:

1. `TTSEngineConfig.from_dict(cls, engine_block, *, base_dir: str | Path | None = None)`. After the existing `module_type` check and **before** the `return`, add the `base_dir` rules from `specs/configuration.md` ("`base_dir` rules"):

   ```python
   module = dict(module_raw)
   loader_dir = Path(base_dir).resolve() if base_dir is not None else None
   if "base_dir" in module:
       raw = module["base_dir"]
       if not isinstance(raw, str) or not raw:
           raise ConfigError("'engine.module.base_dir' must be a non-empty string")
       if loader_dir is not None and not Path(raw).is_absolute():
           module["base_dir"] = str((loader_dir / raw).resolve())
   elif loader_dir is not None:
       module["base_dir"] = str(loader_dir)
   ```

   and return `cls(module=module, player=PlayerConfig(device=device))` (the copy is now `module`, not `dict(module_raw)`). Update the docstring: `type` and `base_dir` are the two keys the loader touches.
2. `TTSEngineConfig.from_json(cls, text, *, base_dir=None)` passes `base_dir=base_dir` to `from_dict`.
3. `TTSEngineConfig.from_json_file(cls, path)` calls `cls.from_dict(_loads(f.read(), source=path), base_dir=Path(path).resolve().parent)`.
4. Same three changes on `MCPServerConfig`: `from_dict(cls, data, *, base_dir=None)` passes `base_dir` into `TTSEngineConfig.from_dict(data["engine"], base_dir=base_dir)`; `from_json` forwards it; `from_json_file` derives it from the file's parent directory.

**A3. Tests in `tests/test_config.py`** (copy the style of `test_from_dict_carries_module_verbatim`). One test per rule:

- `test_from_json_file_fills_module_base_dir(tmp_path)` — write a config with no `base_dir` to `tmp_path / "config.json"`, load with `MCPServerConfig.from_json_file`; assert `cfg.engine.module["base_dir"] == str(tmp_path.resolve())`. Do the same through `TTSEngineConfig.from_json_file` on an engine-block file.
- `test_from_dict_without_base_dir_leaves_module_untouched()` — `TTSEngineConfig.from_dict({"module": {"type": "x"}})`; assert `"base_dir" not in cfg.module`.
- `test_from_dict_with_base_dir_keyword_fills_module(tmp_path)` — `from_dict(block, base_dir=tmp_path)`; assert the module's `base_dir` is `str(tmp_path.resolve())`. Also check `from_json(text, base_dir=...)` gives the same result.
- `test_relative_module_base_dir_is_absolutized_against_loader(tmp_path)` — block has `"base_dir": "fixtures"`, loaded with `base_dir=tmp_path`; assert it became `str((tmp_path / "fixtures").resolve())`.
- `test_relative_module_base_dir_kept_without_loader_base_dir()` — block has `"base_dir": "fixtures"`, loaded with no keyword; assert it is still `"fixtures"`.
- `test_absolute_module_base_dir_kept(tmp_path)` — block has an absolute `base_dir`, loaded with a *different* `base_dir=` keyword; assert it is unchanged.
- `test_module_base_dir_invalid_rejected()` — parametrize over `""`, `3`, `None`, `True`; expect `ConfigError` matching `base_dir`.

**A4. Tests in `tests/modules/test_tts_module_interface.py`** for `resolve_path`:
- absolute path returned unchanged;
- relative path joined onto `config["base_dir"]`;
- relative path joined onto `Path.cwd()` when there is no `base_dir` (use `monkeypatch.chdir(tmp_path)`).

**Checkpoint A:** `uv run ruff check . && uv run ruff format . && uv run pyright && uv run pytest` — all green.

### Part B — the `tone` and `audiofile` fixture modules

**B1. Create `src/tts_engine/modules/tone.py`** following `specs/tone-module.md` exactly. Skeleton:

```python
"""Tone fixture module: a sine tone proportional to the text length.

Not a TTS backend. Exists so the engine, tools, MCP transport, and downstream
test suites run end to end with no API key, extra, model, or fixture files.
"""

import threading
from collections.abc import Callable

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSModule, TTSOptions, run_cancellable_worker

_CHUNK_FRAMES = 4096


def _positive_number(config: dict, field: str, default: float) -> float:
    value = config.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"Tone module '{field}' must be a number > 0")
    return float(value)


class ToneModule(TTSModule):
    def __init__(self, config: dict) -> None:
        sample_rate = config.get("sample_rate", 24000)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or sample_rate <= 0
        ):
            raise ConfigError("Tone module 'sample_rate' must be an integer > 0")
        self._sample_rate = sample_rate
        self._frequency = _positive_number(config, "frequency", 440.0)
        self._seconds_per_char = _positive_number(config, "seconds_per_char", 0.05)
        amplitude = _positive_number(config, "amplitude", 0.2)
        if amplitude > 1.0:
            raise ConfigError("Tone module 'amplitude' must be a number in (0, 1]")
        self._amplitude = amplitude

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream(
        self, text: str, options: TTSOptions, callback: Callable[[bytes], None]
    ) -> None:
        total = round(len(text) * self._seconds_per_char * self._sample_rate)

        def _worker(stop: threading.Event) -> None:
            start = 0
            while start < total and not stop.is_set():
                end = min(start + _CHUNK_FRAMES, total)
                n = np.arange(start, end, dtype=np.float64)
                wave = np.sin(2.0 * np.pi * self._frequency * n / self._sample_rate)
                pcm = np.round(self._amplitude * 32767.0 * wave).astype("<i2")
                callback(pcm.tobytes())
                start = end

        await run_cancellable_worker(_worker)
```

Keep the amplitude check as written (the `_positive_number` helper already rejects `<= 0` and non-numbers; the extra check enforces `<= 1`).

**B2. Create `src/tts_engine/modules/audiofile.py`** following `specs/audiofile-module.md` exactly. Requirements to implement, in constructor order:

1. Read `files` (default `[]`): must be a `list`; each item a `dict` with non-empty `str` `text` and `file`; otherwise `ConfigError` naming the entry index, e.g. `"AudioFile module 'files[2]' must be an object with non-empty 'text' and 'file'"`.
2. Read `default_file` (optional): if present, a non-empty `str`, else `ConfigError`.
3. If `files` is empty and `default_file` is absent: `ConfigError("AudioFile module needs at least one 'files' entry or a 'default_file'")`.
4. Normalize each `text` with `" ".join(text.split())`. A repeated normalized text → `ConfigError` naming the text.
5. Resolve every path with `resolve_path(config, value)` (from `modules/base.py`). Build `self._entries: dict[str, Path]` (normalized text → resolved path) and `self._default: Path | None`.
6. For every distinct path, open it with `wave.open(str(path), "rb")` inside `try/except (OSError, wave.Error) as exc` → `ConfigError(f"AudioFile module cannot read {path}: {exc}")`. Check `getsampwidth() == 2` and `getnchannels() == 1` (else `ConfigError` naming the file and the problem) and that `getframerate()` is the same for all files (else `ConfigError` naming both rates). Store the rate in `self._sample_rate`.

`sample_rate` returns `self._sample_rate`.

`stream(text, options, callback)`: pick `path = self._entries.get(" ".join(text.split()))`, falling back to `self._default`; if both are `None`, `raise TTSError(f"no audio file configured for text: {text!r}")` **before** starting the worker. Then:

```python
def _worker(stop: threading.Event) -> None:
    try:
        wav = wave.open(str(path), "rb")
    except (OSError, wave.Error) as exc:
        raise TTSError(f"AudioFile module cannot read {path}: {exc}") from exc
    with wav:
        while not stop.is_set():
            try:
                chunk = wav.readframes(_CHUNK_FRAMES)
            except (OSError, wave.Error) as exc:
                raise TTSError(f"AudioFile module cannot read {path}: {exc}") from exc
            if not chunk:
                break
            callback(chunk)  # outside the try: callback errors keep their identity


await run_cancellable_worker(_worker)
```

`_CHUNK_FRAMES = 4096`. Import `wave` from the standard library; no other dependency.

**B3. Register both in `src/tts_engine/modules/__init__.py`**: import `ToneModule` and `AudioFileModule` and add `"tone": ToneModule` and `"audiofile": AudioFileModule` to `REGISTRY` (keep `elevenlabs` and `pocket`).

**B4. Pocket `voice` path (`src/tts_engine/modules/pocket.py`).** After validating `voice`, add:

```python
if voice.endswith(".wav") and "://" not in voice:
    voice = str(resolve_path(config, voice))
```

(import `resolve_path` from `tts_engine.modules.base`). Everything else in the file is unchanged.

**B5. Unit tests `tests/modules/test_tone.py`** (no fakes needed; use a list-collecting callback and `asyncio.run` or the async test mode). Cover every bullet of the spec's "Testing" section:
- invalid `sample_rate` (`0`, `True`, `"x"`), `frequency` (`0`), `seconds_per_char` (`-1`), `amplitude` (`0`, `1.5`) → `ConfigError`;
- `ToneModule({"type": "tone"}).sample_rate == 24000`; a configured `8000` is reported;
- streaming `"abcdefghij"` (10 chars) with `seconds_per_char=0.1` at `8000` Hz yields exactly `2 * 8000` bytes and `len % 2 == 0`;
- a 200-char text yields more than one callback;
- the same text twice yields identical bytes;
- a callback that raises `RuntimeError` propagates `RuntimeError`, not `TTSError`;
- cancellation: copy `test_cancel_stops_callbacks_before_stream_raises` from `tests/modules/test_pocket.py` and adapt (a slow callback, cancel the task, assert no callback after `CancelledError`). Use a long text so there are many chunks.

**B6. Unit tests `tests/modules/test_audiofile.py`.** Add a helper that writes a WAV into `tmp_path`:

```python
def _write_wav(path, *, frames=400, rate=8000, channels=1, sampwidth=2):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sampwidth)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames * channels * sampwidth))
```

Then one test per spec bullet: missing file, stereo file, 8-bit file, two files with different rates, duplicate `text` (`"Hello,  world!"` vs `"Hello, world!"`), malformed entry (`{"text": "a"}`), empty config (`{"type": "audiofile"}`) → each `ConfigError`; `sample_rate` equals the fixture rate; an exact match yields exactly `2 * frames` bytes of that file; `"  Hello,   world! "` still matches `"Hello, world!"`; an unmatched text plays `default_file`; unmatched with no default → `TTSError` and the callback list stays empty; a relative `file` resolves against `config["base_dir"]` (pass `"base_dir": str(tmp_path)` and a bare filename); a raising callback propagates unchanged.

**B7. Pocket test.** In `tests/modules/test_pocket.py` add `test_wav_voice_resolves_against_base_dir(monkeypatch, fresh_model)`: config `{"type": "pocket", "voice": "me.wav", "base_dir": "/some/dir"}`; assert the fake model's `get_state_for_audio_prompt` was called with `"/some/dir/me.wav"` (look at how the existing `test_defaults_and_forwarding` inspects the fake to copy the pattern). Also assert a preset like `"alba"` is passed through untouched (the existing default test already does).

**B8. Committed e2e fixtures.** Create `tests-e2e/fixtures/` and generate two half-second, 24 kHz, 16-bit mono WAV files with this one-off script (run it once, commit the outputs, do not commit the script):

```bash
uv run python - <<'PY'
import math, wave
from pathlib import Path
out = Path("tests-e2e/fixtures"); out.mkdir(exist_ok=True)
for name, freq in (("hello.wav", 440.0), ("default.wav", 660.0)):
    with wave.open(str(out / name), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"".join(
            int(0.2 * 32767 * math.sin(2 * math.pi * freq * n / 24000)).to_bytes(2, "little", signed=True)
            for n in range(12000)))
PY
```

**B9. `tests-e2e/support.py`.**
- Add the two rows to `MODULES`:
  ```python
  pytest.param("tone", {}, id="tone"),
  pytest.param(
      "audiofile",
      {
          "base_dir": str(Path(__file__).parent / "fixtures"),
          "default_file": "default.wav",
          "files": [{"text": "Hello directly from the TTS engine", "file": "hello.wav"}],
      },
      id="audiofile",
  ),
  ```
  (add `from pathlib import Path` to the imports).
- Change `default_module()` to return `("tone", {})` and update its docstring: the default is the `tone` fixture module so the transport test never skips; it says nothing about which provider users should pick.
- Update the module docstring and the comment above `_ELEVENLABS_CONFIG` (it is now used only by the `elevenlabs` `MODULES` row, not by `default_module()`).

**Checkpoint B:** `uv run ruff check . && uv run ruff format . && uv run pyright && uv run pytest` green; then `uv run pytest tests-e2e -k "tone or audiofile"` — all four cases pass on a machine with audio hardware, and `tests-e2e/test_mcp.py` passes **without** any `ELEVENLABS_API_KEY` set.

### Part C — ElevenLabs behind an extra, base install provider-agnostic

**C1. `pyproject.toml`.**
- Remove `"elevenlabs"` and `"miniaudio"` from `[project] dependencies`.
- Under `[project.optional-dependencies]` add `elevenlabs = ["elevenlabs", "miniaudio"]` and `all = ["tts-engine[elevenlabs,pocket]"]` (keep `pocket`). Replace the comment block above it with: one extra per provider, the base is provider-agnostic and has no default provider, each provider imports lazily and raises `ConfigError` with a `pip install tts-engine[<name>]` hint.
- Add `"elevenlabs"` and `"miniaudio"` to the `dev` group with a comment: light enough to keep in dev so the ElevenLabs unit tests run unchanged; `pocket` stays faked.
- Run `uv sync --dev` so `uv.lock` is updated; commit the lock.

**C2. Lazy imports in `src/tts_engine/modules/elevenlabs.py`.**
- Delete the three top-level imports `import miniaudio`, `from elevenlabs import ElevenLabs`, `from elevenlabs.types import VoiceSettings`.
- Replace the module-level `class _ChunkSource(miniaudio.StreamableSource)` with a factory that builds it from the imported module:
  ```python
  def _make_chunk_source_class(miniaudio_mod):
      class _ChunkSource(miniaudio_mod.StreamableSource):
          """Wraps a bytes-chunk iterator as a miniaudio StreamableSource."""

          # ... same __init__ and read as today ...

      return _ChunkSource
  ```
- At the **top** of `__init__` (before `_resolve_api_key`), add the lazy import exactly as in `specs/elevenlabs-module.md` ("Dependencies and lazy import"), then store `self._miniaudio = miniaudio`, `self._voice_settings_cls = VoiceSettings`, `self._chunk_source_cls = _make_chunk_source_class(miniaudio)`, and build the client with the imported `ElevenLabs`.
- In `stream()`, replace `VoiceSettings(...)` with `self._voice_settings_cls(...)`, `miniaudio.stream_any` / `miniaudio.SampleFormat.SIGNED16` with `self._miniaudio.stream_any` / `self._miniaudio.SampleFormat.SIGNED16`, and `_ChunkSource(raw_chunks)` with `self._chunk_source_cls(raw_chunks)`.
- Nothing else changes (validation order, `sample_rate`, error handling).

**C3. Adapt `tests/modules/test_elevenlabs.py`.** The SDK client is currently patched at `tts_engine.modules.elevenlabs.ElevenLabs`; with the lazy import that name no longer exists in the module. Patch `elevenlabs.ElevenLabs` instead (the attribute the lazy `from elevenlabs import ElevenLabs` resolves at construction time). Check `grep -n "patch(" tests/modules/test_elevenlabs.py` and update every target; keep every assertion as it is. Add one test: `test_missing_extra_raises_config_error(monkeypatch)` — `monkeypatch.setitem(sys.modules, "elevenlabs", None)` (and `"miniaudio"`), then `ElevenLabsModule(VALID_CONFIG)` raises `ConfigError` matching `tts-engine\[elevenlabs\]`. Mirror `test_missing_extra_raises_config_error` in `tests/modules/test_pocket.py`.

**C4. New guard `tests/modules/test_lazy_imports.py`.** Copy the subprocess pattern of `tests/test_no_audio_import.py`. The script poisons `sys.modules["elevenlabs"] = None`, `["elevenlabs.types"] = None`, `["miniaudio"] = None`, `["pocket_tts"] = None`, `["torch"] = None`, then:
- `import tts_engine` and `from tts_engine.modules import REGISTRY`; assert `set(REGISTRY) == {"elevenlabs", "pocket", "tone", "audiofile"}`;
- constructing `REGISTRY["elevenlabs"]({"type": "elevenlabs", "api_key": "k", "voice_id": "v"})` raises `ConfigError` whose message contains `tts-engine[elevenlabs]`;
- constructing `REGISTRY["pocket"]({"type": "pocket"})` raises `ConfigError` whose message contains `tts-engine[pocket]`;
- constructing `REGISTRY["tone"]({"type": "tone"})` succeeds;
- prints `OK`. The test asserts return code 0 and `OK` in stdout.

**C5. `tests/test_mcp_server_cli.py`.** Replace the `elevenlabs` module block in `_CONFIG` with `{"type": "tone"}`. The test patches `TTSEngine`, so no module is built; the point is that the entry-point test no longer names a provider.

**C6. `tests-e2e/support.py`.** Add `"elevenlabs": "elevenlabs"` to `_REQUIRED_IMPORT` and rewrite its comment: one row per provider extra; fixture modules declare nothing.

**C7. Examples.** Delete `config.example.json`. Create `examples/` with four files, each a complete `{engine, server}` config (`server` = `127.0.0.1:8000`, `player.device` = `null`):
- `config.elevenlabs.json` — the current `config.example.json` module block.
- `config.pocket.json` — `{"type": "pocket", "voice": "alba", "device": "auto"}`.
- `config.tone.json` — `{"type": "tone"}`. JSON has no comments, so the "fixture module, not a TTS" warning goes in the README next to the examples, not in the file.
- `config.audiofile.json` — the example block from `specs/audiofile-module.md`, with paths `fixtures/hello.wav`, `fixtures/goodbye.wav`, `fixtures/default.wav` (placeholders the user supplies; relative to the file's directory).
Then `grep -rn "config.example.json" . --exclude-dir=.venv --exclude-dir=.git` and fix every hit (AGENTS.md, README.md, specs are already updated).

**Checkpoint C:** `uv sync --dev`, then `uv run ruff check . && uv run ruff format . && uv run pyright && uv run pytest` green. Then prove the base install is provider-free: `uv sync --no-dev` (no extras) and `uv run python -c "import tts_engine; from tts_engine.modules import REGISTRY; print(sorted(REGISTRY))"` prints all four types; `uv run tts-engine-mcp --config examples/config.elevenlabs.json` exits with a `ConfigError` mentioning `tts-engine[elevenlabs]`. Run `uv sync --dev` again afterwards. Finally, with the key sourced (`zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv sync --dev --extra elevenlabs && uv run pytest tests-e2e'`): every row runs or skips cleanly, none fails.

### Part D — docs and statuses

**D1. `AGENTS.md`.** Update, keeping the structure:
- "What this project is": "(ElevenLabs first)" → "(a provider installed as an extra — ElevenLabs or pocket-tts — or a built-in fixture module for tests)".
- "Key design decisions": the "MP3 from ElevenLabs" bullet stays but add a bullet "**No default provider**: the base install is provider-agnostic; every provider is an extra (`elevenlabs`, `pocket`, `all`); `tone` and `audiofile` are fixture modules for tests and demos, never presented as TTS" and a bullet on `base_dir` (reserved module key, filled by the file loaders, resolved by `resolve_path`).
- Project map: `config.example.json` row → `examples/` row; `modules/` row lists `tone.py`, `audiofile.py` and links `specs/tone-module.md`, `specs/audiofile-module.md`; `tests-e2e/` row mentions the fixture rows never skip and `fixtures/`.
- "Entry points": `uv sync --dev` line gets `uv sync --dev --extra elevenlabs` / `--extra pocket` / `--all-extras` beside it; `--config config.json` → `--config examples/config.tone.json` in the example.
- "Config structure": keep the ElevenLabs example but say it is *one* provider example and point at `examples/`; mention `base_dir`.
- "Data flow": add `tone: sine` and `audiofile: WAV file` lines beside the `elevenlabs`/`pocket` ones.
- "Testing": the default e2e module is `tone`; `_REQUIRED_IMPORT` has one row per provider extra.
- "Adding a new TTS module": step 1 says a provider goes behind an extra with a lazy import (link project.md); step 3 also says to add an `examples/config.<name>.json`.

**D2. `README.md`.** Installation: the base install has no provider; show `pip install "tts-engine[elevenlabs]"`, `[pocket]`, `[all]`. Quick start: choose a provider first; a "no key? try the tone module" note. "TTS modules" table: four rows, with a "Kind" column (provider / fixture) and the install column; add `### tone` and `### audiofile` sections after `### pocket`, both opening with "fixture module for tests and demos, not a TTS backend". Configuring: mention `examples/` and `base_dir`. Troubleshooting: an "ElevenLabs module requires the elevenlabs extra" entry.

**D3. Status upkeep.** Flip `**Status:**` to `Implemented` in `specs/configuration.md`, `tts-module-interface.md`, `project.md`, `testing.md`, `elevenlabs-module.md`, `pocket-module.md`, `architecture.md`, `tone-module.md`, `audiofile-module.md`; mirror in `specs/_index.md`. Grow the frontmatter of `tone-module.md` (`code:` add `src/tts_engine/modules/tone.py`; `tests:` add `tests/modules/test_tone.py`) and `audiofile-module.md` (`modules/audiofile.py`, `tests/modules/test_audiofile.py`); add `tests/modules/test_lazy_imports.py` to the `tests:` of `elevenlabs-module.md` and `project.md`; add `tests-e2e/fixtures/hello.wav` and `default.wav` to the `code:` list of `testing.md`. Mark this plan `Done` here and in `plans/_index.md`.

## Verification

- After each part: `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all green, unit tier under 5 s.
- `uv run pytest tests/test_project_map.py` passes after D3 (every frontmatter path exists, every module governed).
- Live tier, no key, no extras: `uv run pytest tests-e2e` — `tone`, `audiofile`, and `test_mcp.py` **pass**; `elevenlabs` and `pocket` rows **skip** with the extra/key message.
- Live tier with the key and the extra (see Checkpoint C) — `elevenlabs` rows pass.
- `grep -rn -i "reference provider\|base install includes\|ElevenLabs first" README.md AGENTS.md specs` returns nothing.
- Mark this plan `Done` (here and in [_index.md](_index.md)) only once all of the above pass.
