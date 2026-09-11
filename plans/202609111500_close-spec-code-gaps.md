# Close spec/code gaps: cancellation, callback errors, validation, player lifecycle

**Status:** Done

Implements behavior that [elevenlabs-module.md](../specs/elevenlabs-module.md), [tts-module-interface.md](../specs/tts-module-interface.md), [audio-player.md](../specs/audio-player.md), [configuration.md](../specs/configuration.md), and [architecture.md](../specs/architecture.md) already describe but the code never got (a repo audit on 2026-09-11 found the specs were reconciled in prose in commit `7d486f1` without matching code). Five behavioral gaps plus one test-hygiene fix:

| # | Gap | Spec that promises it | Where the code falls short today |
|---|---|---|---|
| A1 | Cancelling `stream()` must stop the worker thread before `CancelledError` propagates — no `callback` call after `stream()` raises | tts-module-interface.md "TTSModule ABC" docstring; elevenlabs-module.md "Output format and decoding" + "Error handling"; architecture.md "Threading / async model" | Both modules do a bare `await asyncio.to_thread(...)`; cancelling the task does **not** stop the thread, which keeps calling `callback` after the engine has already `drain()`ed the sink |
| A2 | Exceptions raised by `callback` propagate **unchanged** (never relabeled as a provider error) | elevenlabs-module.md "Error handling"; tts-module-interface.md "Error handling"; mcp-server.md "Return value (error)" | `elevenlabs.py` wraps the whole feed loop, callback included, in `except Exception → TTSError`. (`pocket.py` already does this correctly.) |
| A3 | ElevenLabs config fields are validated at construction | elevenlabs-module.md "API key resolution" (last paragraph) | Only `voice_id` truthiness is checked; `stability=5`, `stability=True`, `model=123`, `api_key=123`, `voice_id=42` are all accepted |
| A4 | `AudioPlayer`: a failed `start()` closes the new stream; `drain()` always `close()`s even if `stop()` raises | audio-player.md "Stream lifecycle" | Neither is done |
| A5 | `server.host` must be a non-empty string | configuration.md "`server` block" + "Validation rules" | Not checked |
| C1 | Unit tests never assert private attributes | testing.md "What a good test asserts" | `test_elevenlabs.py::test_defaults` / `test_custom_values` read `module._model`, `_stability`, `_similarity_boost` |

Deliberately unchanged: the `TTSModule` contract, the config file shape, the public API (`__all__`), and the engine. No new config fields, no new exports.

## How to work this plan

- Do the steps **in order**; each one's "Done when" must hold before moving on. Steps 1–6 are code, 7–10 are tests, 11 is specs, 12 is verification.
- The code blocks below are the **complete target code** for each function/block, not sketches — replace the existing code with them as written. Where a block is a *whole file*, it says so.
- Indentation in the blocks is whatever `ruff format` left in this markdown file: some method bodies are shown at column 0. When a step says the code goes inside a class or function, re-indent it to match its surroundings — `uv run ruff format .` will not fix wrong nesting for you.
- After every step run `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`. All four must stay green (the suite must also stay under 5 s — see [testing.md](../specs/testing.md), "Speed budget").
- Keep [AGENTS.md](../AGENTS.md) conventions: logger variable is `log`; library code never configures logging; tests assert observable behavior only.
- **Do not** try to fix cancellation with `asyncio.wait_for`, `task.cancel()` on the thread's future, or `concurrent.futures.Future.cancel()` — a running executor thread cannot be cancelled from outside; the only working mechanism is the cooperative `stop` flag the worker polls.

## Scope

- `src/tts_engine/modules/base.py` — add `run_cancellable_worker()` (step 1).
- `src/tts_engine/modules/elevenlabs.py` — use the helper; restructure the feed loop; add validation (steps 2–3).
- `src/tts_engine/modules/pocket.py` — use the helper (step 4).
- `src/tts_engine/audio.py` — `feed`/`drain` lifecycle safety (step 5).
- `src/tts_engine/config.py` — `server.host` check (step 6).
- `tests/modules/test_elevenlabs.py` — replace two private-attr tests; add validation, callback-error, and cancellation tests (step 7).
- `tests/modules/test_pocket.py` — add a cancellation test (step 8).
- `tests/test_audio.py` — add two lifecycle tests (step 9).
- `tests/test_config.py` — add `server.host` tests (step 10).
- `specs/tts-module-interface.md`, `specs/elevenlabs-module.md`, `specs/audio-player.md`, `specs/configuration.md`, `specs/_index.md` — status `Updated` → `Implemented`; one new paragraph and one refreshed code sketch (step 11).
- `plans/_index.md` — this plan's row → `Done` (step 12).

## Steps

### Step 1 — `run_cancellable_worker` helper (`src/tts_engine/modules/base.py`)

Add these imports at the top of the file (keep the existing ones):

```python
import asyncio
import threading
```

Append this function at the **end** of the file, after the `TTSModule` class:

```python
async def run_cancellable_worker(worker: Callable[[threading.Event], None]) -> None:
    """Run a blocking ``worker`` in a thread with cooperative cancellation.

    ``worker(stop)`` is executed via ``asyncio.to_thread``. It must poll
    ``stop.is_set()`` between chunks and return promptly once it is set.

    If the awaiting coroutine is cancelled, this sets ``stop``, waits for the
    thread to actually exit, and only then re-raises ``CancelledError`` — so a
    module's ``stream()`` never returns or raises while its worker can still
    invoke ``callback``. Any exception the worker raises while unwinding after
    a cancel is discarded (the caller's outcome is the cancellation). Without
    a cancel, the worker's exception propagates unchanged.
    """
    stop = threading.Event()
    task = asyncio.ensure_future(asyncio.to_thread(worker, stop))
    try:
        # shield: cancelling *us* must not cancel the executor future — we
        # still need to await it below so the thread has really stopped.
        await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await asyncio.wait([task])
        if not task.cancelled():
            task.exception()  # mark retrieved so asyncio doesn't log it at GC
        raise
```

**Done when:** `uv run pyright` and `uv run ruff check .` pass; nothing else changes yet.

### Step 2 — ElevenLabs feed loop (`src/tts_engine/modules/elevenlabs.py`)

Change the imports block to:

```python
import os
import threading
from collections.abc import Callable, Iterator

import miniaudio
from elevenlabs import ElevenLabs
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
)
```

(`import asyncio` is no longer needed in this file — remove it.)

Replace the whole `stream` method with:

```python
    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        def _blocking_stream(stop: threading.Event) -> None:
            # Build the provider request and the decoder inside the provider
            # try so SDK/decoder failures become TTSError. (The generator
            # expression evaluates the SDK call eagerly, so it fails here.)
            try:
                raw_chunks = (
                    chunk
                    for chunk in self._client.text_to_speech.stream(
                        text=text,
                        voice_id=self._voice_id,
                        model_id=self._model,
                        output_format="mp3_44100_128",
                        voice_settings=VoiceSettings(
                            stability=self._stability,
                            similarity_boost=self._similarity_boost,
                        ),
                    )
                    if chunk
                )
                pcm_iter = iter(
                    miniaudio.stream_any(
                        _ChunkSource(raw_chunks),
                        output_format=miniaudio.SampleFormat.SIGNED16,
                        nchannels=1,
                        sample_rate=44100,
                    )
                )
            except Exception as exc:
                raise TTSError(f"ElevenLabs request failed: {exc}") from exc

            while not stop.is_set():
                # Advance the decoder inside the provider try so SDK/decoder
                # errors become TTSError; feed the callback outside it so a
                # downstream playback failure keeps its own identity.
                try:
                    pcm_chunk = next(pcm_iter)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"ElevenLabs request failed: {exc}") from exc
                callback(pcm_chunk.tobytes())

        await run_cancellable_worker(_blocking_stream)
```

**Done when:** the three existing `test_stream_*` tests in `tests/modules/test_elevenlabs.py` still pass unchanged.

### Step 3 — ElevenLabs validation (`src/tts_engine/modules/elevenlabs.py`)

Add this module-level helper **above** `class ElevenLabsModule`:

```python
def _unit_interval(config: dict, field: str, default: float) -> float:
    """Read an optional numeric field that must lie in [0, 1]; bools rejected."""
    value = config.get(field, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (0.0 <= value <= 1.0)
    ):
        raise ConfigError(
            f"ElevenLabs module '{field}' must be a number between 0 and 1"
        )
    return float(value)
```

Replace `__init__` with:

```python
    def __init__(self, config: dict) -> None:
        api_key = self._resolve_api_key(config)

        voice_id = config.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            raise ConfigError("ElevenLabs module requires a non-empty 'voice_id'")

        model = config.get("model", "eleven_flash_v2_5")
        if not isinstance(model, str) or not model:
            raise ConfigError("ElevenLabs module 'model' must be a non-empty string")

        self._voice_id: str = voice_id
        self._model: str = model
        self._stability: float = _unit_interval(config, "stability", 0.5)
        self._similarity_boost: float = _unit_interval(config, "similarity_boost", 0.75)
        self._client = ElevenLabs(api_key=api_key)
```

Replace `_resolve_api_key` with (type checks first, then the existing precedence):

```python
    @staticmethod
    def _resolve_api_key(config: dict) -> str:
        """Resolve the API key from a literal ``api_key`` or, failing that, the
        environment variable named by ``api_key_env`` — so a config file can be
        committed with only the env-var name and no secret."""
        api_key = config.get("api_key")
        env_name = config.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError("ElevenLabs module 'api_key' must be a string")
        if env_name is not None and not isinstance(env_name, str):
            raise ConfigError("ElevenLabs module 'api_key_env' must be a string")

        if api_key:
            return api_key

        if env_name:
            api_key = os.environ.get(env_name)
            if not api_key:
                raise ConfigError(
                    f"ElevenLabs module: environment variable {env_name!r} "
                    "(named by 'api_key_env') is unset or empty"
                )
            return api_key

        raise ConfigError(
            "ElevenLabs module requires a non-empty 'api_key' or 'api_key_env'"
        )
```

Precedence is unchanged: a non-empty literal `api_key` wins; an empty `api_key` (`""`) falls through to `api_key_env` exactly as before.

**Done when:** all existing `tests/modules/test_elevenlabs.py` tests pass (the two private-attribute tests still pass at this point; they are replaced in step 7).

### Step 4 — Pocket cancellation (`src/tts_engine/modules/pocket.py`)

Change the imports: remove `import asyncio`, add `import threading`, and import the helper:

```python
import threading
from collections.abc import Callable

import numpy as np

from tts_engine.config import ConfigError
from tts_engine.modules.base import (
    TTSError,
    TTSModule,
    TTSOptions,
    run_cancellable_worker,
)
```

Replace the whole `stream` method with:

```python
    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        max_tokens = self._max_tokens

        def _blocking_stream(stop: threading.Event) -> None:
            # Pass max_tokens only when configured, else the library's own
            # default (50) applies — None would override it and is rejected.
            if max_tokens is None:
                stream_iter = self._model.generate_audio_stream(self._voice_state, text)
            else:
                stream_iter = self._model.generate_audio_stream(
                    self._voice_state, text, max_tokens=max_tokens
                )
            while not stop.is_set():
                # Advance the generator inside the provider try so inference
                # errors become TTSError; feed the callback outside it so a
                # downstream playback failure keeps its own identity.
                try:
                    chunk = next(stream_iter)
                except StopIteration:
                    break
                except Exception as exc:
                    raise TTSError(f"pocket-tts synthesis failed: {exc}") from exc
                callback(_to_int16_pcm(chunk))

        await run_cancellable_worker(_blocking_stream)
```

The only differences from today are `stop: threading.Event` in the signature, `while not stop.is_set():` instead of `while True:`, and the final `await`.

**Done when:** all existing `tests/modules/test_pocket.py` tests pass.

### Step 5 — `AudioPlayer` lifecycle (`src/tts_engine/audio.py`)

Replace the `feed` and `drain` methods of `AudioPlayer` with the two functions below (shown at column 0 — indent them by 4 spaces as methods of the class; `__init__` stays as it is):

```python
def feed(self, chunk: bytes) -> None:
    if not chunk:
        return
    if self._stream is None:
        # Imported lazily (not at module top) so `import tts_engine` and an
        # engine driven by a custom sink work on hosts with no PortAudio.
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=_CHANNELS,
            dtype=_DTYPE,
            device=self._device,
        )
        try:
            stream.start()
        except Exception:
            # Never leave a half-opened stream attached: release the
            # device, and let the next feed() try afresh.
            stream.close()
            raise
        self._stream = stream
    array = np.frombuffer(chunk, dtype=np.int16)
    self._stream.write(array)


def drain(self) -> None:
    # Detach first so a failing stop() cannot leave a stale stream on the
    # player; close() always runs so the device is released either way.
    stream, self._stream = self._stream, None
    if stream is None:
        return
    try:
        stream.stop()
    finally:
        stream.close()
```

**Done when:** all existing `tests/test_audio.py` tests pass.

### Step 6 — `server.host` validation (`src/tts_engine/config.py`)

In `MCPServerConfig.from_dict`, replace the block from `port = server_raw.get("port", 8000)` down to the `server_cfg = ...` line with the following (shown at column 0 — indent by 8 spaces to sit inside the method, same as the lines it replaces):

```python
host = server_raw.get("host", "127.0.0.1")
if not isinstance(host, str) or not host:
    raise ConfigError("'server.host' must be a non-empty string")
port = server_raw.get("port", 8000)
if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
    raise ConfigError(
        f"'server.port' must be an integer in range 1–65535, got {port!r}"
    )
server_cfg = ServerConfig(host=host, port=port)
```

**Done when:** all existing `tests/test_config.py` tests pass.

### Step 7 — ElevenLabs tests (`tests/modules/test_elevenlabs.py`)

Change the imports at the top to:

```python
import array as _array
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from elevenlabs.types import VoiceSettings

from tts_engine.config import ConfigError
from tts_engine.modules.base import TTSError, TTSOptions
from tts_engine.modules.elevenlabs import ElevenLabsModule
```

**7a.** Delete `test_defaults` and `test_custom_values` (they assert private attributes). Add in their place:

```python
async def _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config) -> dict:
    """Run one stream() and return the kwargs the module sent to the SDK.

    Provider configuration is observed through the arguments sent to the
    provider client (testing.md), never through private attributes.
    """
    mock_stream_any.return_value = iter([])
    mock_client = mock_elevenlabs_cls.return_value
    mock_client.text_to_speech.stream.return_value = iter([b"mp3"])
    module = ElevenLabsModule(config)
    await module.stream("hello", TTSOptions(), MagicMock())
    return mock_client.text_to_speech.stream.call_args.kwargs


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_defaults_reach_the_provider_client(mock_elevenlabs_cls, mock_stream_any):
    kwargs = await _provider_stream_kwargs(
        mock_elevenlabs_cls, mock_stream_any, VALID_CONFIG
    )
    assert kwargs["voice_id"] == "test-voice-id"
    assert kwargs["model_id"] == "eleven_flash_v2_5"
    assert kwargs["output_format"] == "mp3_44100_128"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.5, similarity_boost=0.75
    )


@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_custom_values_reach_the_provider_client(
    mock_elevenlabs_cls, mock_stream_any
):
    config = {
        **VALID_CONFIG,
        "model": "eleven_multilingual_v2",
        "stability": 0.8,
        "similarity_boost": 0.9,
    }
    kwargs = await _provider_stream_kwargs(mock_elevenlabs_cls, mock_stream_any, config)
    assert kwargs["model_id"] == "eleven_multilingual_v2"
    assert kwargs["voice_settings"] == VoiceSettings(
        stability=0.8, similarity_boost=0.9
    )
```

**7b.** Add the validation test (one parametrized test; the `match` is the field name):

```python
@pytest.mark.parametrize(
    "override, field",
    [
        ({"api_key": 123}, "api_key"),
        ({"api_key_env": 5}, "api_key_env"),
        ({"voice_id": 42}, "voice_id"),
        ({"model": ""}, "model"),
        ({"model": 7}, "model"),
        ({"stability": 5}, "stability"),
        ({"stability": True}, "stability"),
        ({"similarity_boost": -0.1}, "similarity_boost"),
        ({"similarity_boost": "x"}, "similarity_boost"),
    ],
)
def test_invalid_field_raises_config_error(override, field):
    with pytest.raises(ConfigError, match=field):
        ElevenLabsModule({**VALID_CONFIG, **override})
```

**7c.** Add the callback-error test (A2):

```python
@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
def test_stream_does_not_mask_callback_errors(mock_elevenlabs_cls, mock_stream_any):
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])
    mock_stream_any.return_value = iter([_array.array("h", [1, 2])])

    def bad_callback(_chunk: bytes) -> None:
        raise ValueError("playback failed")

    module = ElevenLabsModule(VALID_CONFIG)
    with pytest.raises(ValueError, match="playback failed"):
        asyncio.run(module.stream("hello", TTSOptions(), bad_callback))
```

**7d.** Add the cancellation test (A1). It must be `async` (the suite runs with `asyncio_mode = "auto"`) so the running loop is available:

```python
@patch("tts_engine.modules.elevenlabs.miniaudio.stream_any")
@patch("tts_engine.modules.elevenlabs.ElevenLabs")
async def test_cancel_stops_callbacks_before_stream_raises(
    mock_elevenlabs_cls, mock_stream_any
):
    # An endless decoder: without cooperative cancellation the worker thread
    # would keep calling back forever after stream() has raised.
    def endless_chunks(*args, **kwargs):
        while True:
            time.sleep(0.005)
            yield _array.array("h", [1])

    mock_stream_any.side_effect = endless_chunks
    mock_elevenlabs_cls.return_value.text_to_speech.stream.return_value = iter([b"x"])

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    module = ElevenLabsModule(VALID_CONFIG)
    task = asyncio.create_task(module.stream("hello", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)  # ~10 chunk periods: a leaked worker would show
    assert count == settled
```

**Done when:** `uv run pytest tests/modules/test_elevenlabs.py -q` passes and the file contains no `module._` attribute reads (`grep -n "module\._" tests/modules/test_elevenlabs.py` prints nothing).

### Step 8 — Pocket cancellation test (`tests/modules/test_pocket.py`)

Add `import asyncio` and `import time` to the top-level imports (the file currently does `import asyncio` inside several tests; leave those as they are).

In `_FakeModel.__init__` add one attribute after `self.raise_on_stream`:

```python
        self.endless = False
```

Replace `_FakeModel.generate_audio_stream` with:

```python
    def generate_audio_stream(self, state, text, **kwargs):
        self.stream_kwargs = kwargs
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        if self.endless:
            while True:
                time.sleep(0.005)
                yield _FakeChunk([0.1])
        yield from self.chunks
```

Append this test under the `# --- streaming ---` section:

```python
async def test_cancel_stops_callbacks_before_stream_raises(monkeypatch, fresh_model):
    _install_fakes(monkeypatch)
    fresh_model.endless = True
    module = PocketModule({"type": "pocket"})

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()
    count = 0

    def callback(_chunk: bytes) -> None:
        nonlocal count
        count += 1
        loop.call_soon_threadsafe(first_chunk.set)

    task = asyncio.create_task(module.stream("hi", TTSOptions(), callback))
    await first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = count
    await asyncio.sleep(0.05)
    assert count == settled
```

**Done when:** `uv run pytest tests/modules/test_pocket.py -q` passes.

### Step 9 — `AudioPlayer` lifecycle tests (`tests/test_audio.py`)

Add `import pytest` to the imports. Append:

```python
def test_start_failure_closes_stream_and_leaves_player_unopened(mocker):
    mock_stream = mocker.MagicMock()
    mock_stream.start.side_effect = RuntimeError("device busy")
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)
    player = AudioPlayer(sample_rate=44100)

    with pytest.raises(RuntimeError, match="device busy"):
        player.feed(_pcm_bytes())

    # The half-opened stream is released and nothing was written to it.
    mock_stream.close.assert_called_once()
    mock_stream.write.assert_not_called()
    # Nothing is attached: drain is a no-op and a later feed opens afresh.
    player.drain()
    mock_stream.stop.assert_not_called()
    mock_stream.start.side_effect = None
    player.feed(_pcm_bytes())
    assert mock_ctor.call_count == 2


def test_drain_closes_stream_even_if_stop_raises(mocker):
    mock_stream = mocker.MagicMock()
    mock_stream.stop.side_effect = RuntimeError("stop failed")
    mock_ctor = mocker.patch("sounddevice.OutputStream", return_value=mock_stream)
    player = AudioPlayer(sample_rate=44100)
    player.feed(_pcm_bytes())

    with pytest.raises(RuntimeError, match="stop failed"):
        player.drain()

    # close() still ran, the stream is detached, and the player is reusable.
    mock_stream.close.assert_called_once()
    player.drain()  # no-op: nothing attached any more
    assert mock_stream.stop.call_count == 1
    player.feed(_pcm_bytes())  # a fresh stream is opened
    assert mock_ctor.call_count == 2
```

**Done when:** `uv run pytest tests/test_audio.py -q` passes.

### Step 10 — `server.host` tests (`tests/test_config.py`)

Append after `test_port_bool_rejected`:

```python
@pytest.mark.parametrize("bad", ["", 123, None])
def test_host_invalid_rejected(bad):
    data = {**VALID, "server": {"host": bad, "port": 8000}}
    with pytest.raises(ConfigError, match="host"):
        MCPServerConfig.from_dict(data)
```

(`test_server_defaults_when_omitted` already covers the default `127.0.0.1` — keep it.)

**Done when:** `uv run pytest tests/test_config.py -q` passes.

### Step 11 — Specs

**11a. `specs/tts-module-interface.md`** — insert this new subsection immediately **before** `## Local-model modules`:

```markdown
## Cancellation

`stream()` must not return or raise while its worker thread can still call `callback` — otherwise the engine would `drain()` the sink (closing the player) while chunks are still arriving, and release its `say` lock while an old utterance is still playing. A running `asyncio.to_thread` worker cannot be cancelled from outside, so cancellation is **cooperative**: every module runs its blocking loop through `run_cancellable_worker(worker)` in `modules/base.py`. The helper hands the worker a `threading.Event` (`stop`); the worker polls `stop.is_set()` between chunks and returns as soon as it is set. On `CancelledError` the helper sets the flag, waits for the thread to exit, and only then re-raises. A module written this way satisfies the "returns or raises only after it has stopped invoking `callback`" clause of the ABC docstring for free.
```

Then change `**Status:** Updated` → `**Status:** Implemented`.

**11b. `specs/elevenlabs-module.md`** — replace the second half of the code block in "Output format and decoding" (the part starting `async def stream(self, text, options, callback):` through its `...`) with:

```python
async def stream(self, text, options, callback):
    def _blocking_stream(stop: threading.Event) -> None:
        # Build the provider request + decoder inside the TTSError boundary,
        # then loop: advance the decoder inside that boundary, invoke callback
        # outside it (so playback failures keep their identity), and poll
        # `stop` between chunks for cooperative cancellation.
        ...

    await run_cancellable_worker(_blocking_stream)
```

In the paragraph right after that block, replace "On coroutine cancellation, a thread-safe stop flag requests termination between decoded chunks and the coroutine waits for the worker to finish before propagating `CancelledError`; no callback can occur after `stream()` exits." with "Cancellation is cooperative via `run_cancellable_worker` (see [tts-module-interface.md](tts-module-interface.md), "Cancellation"): the worker polls the stop flag between decoded chunks, and the coroutine waits for the thread to finish before propagating `CancelledError`, so no callback can occur after `stream()` exits."

Then change `**Status:** Updated` → `**Status:** Implemented`.

**11c. `specs/audio-player.md`** and **`specs/configuration.md`** — prose already describes the implemented behavior; change `**Status:** Updated` → `**Status:** Implemented` in each.

**11d. `specs/_index.md`** — change the `Updated` cell to `Implemented` on the rows for `configuration.md`, `tts-module-interface.md`, `elevenlabs-module.md`, and `audio-player.md`.

**Done when:** `grep -n "Updated" specs/*.md` prints only the status-legend lines in `_index.md` (and nothing in the four specs).

### Step 12 — Verification and status

Run, in this order, and confirm each is clean:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q          # expect: all passed, total time < 5 s
```

Optional but recommended (needs the live keys/extra; see [AGENTS.md](../AGENTS.md) "Testing"): `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run pytest tests-e2e'` — both backends must still synthesize end-to-end.

Then set this plan's `**Status:**` to `Done` (top of this file) and its row in [_index.md](_index.md) to `Done`.

## Verification (summary)

Lint, format check, type check, and the unit tier all pass. New coverage: no callback after `stream()` raises `CancelledError` (both modules); ElevenLabs callback exceptions escape as themselves; ElevenLabs field validation; `AudioPlayer` start/stop failure paths; `server.host` validation; `test_elevenlabs.py` reads no private attributes. The four specs return to `Implemented`; this plan is `Done`.
