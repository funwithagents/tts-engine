---
code:
  - examples/setup_demo/__init__.py
  - examples/setup_demo/__main__.py
  - examples/setup_demo/app.py
  - examples/setup_demo/session.py
  - pyproject.toml
tests:
  - tests/examples/test_setup_demo.py
---

# Setup demo

**Status:** Implemented

## Purpose

A page to try the engine without writing a config by hand: choose a voice, configure it, install its extra, download its model, then hear it. It is the engine's demo and the first app built on [setup.md](setup.md): the page itself (the Setup, Secrets and Session tabs, the app's folder, the sessions) comes from the `module-setup` project, whose specs are the reference for it; this spec says what the engine's demo puts in it.

## Running it

```
uv run python -m examples.setup_demo [--folder PATH] [--port N] [--host ADDRESS] [--no-autostart]
```

The page opens on its Setup tab with the `tone` fixture, which needs nothing installed. The app's folder (its saved config, settings and `.env`) is `~/.config/tts-engine-demo` unless `--folder` names another.

`module-setup[ui]` (and through it gradio) is the `demo` dependency group, synced by default with `dev`: it is never a dependency of the published package.

## What the demo declares

- **One section, `tts`**, whose document is the engine block (`TTSEngineConfig.from_dict`'s), described by `tts_engine.setup`.
- **The host is `tts-engine` itself**, so "Install missing extras" installs this project's own extras (`phonon`, `kokoro`, …) from its lock.
- **The default config** is `{"tts": {"module": {"type": "tone"}}}`.
- **The session** is a `SpeechSession`: a `TTSEngine` built from the saved section, a relative path in it resolved against the app's folder, running on an event loop of its own so the page's handlers can wait for it. `stop` interrupts what is being said, closes the engine (its model, its device) and ends the loop. A config the engine refuses fails the start, its message on the page, and leaves no thread behind.
- **The Session tab** has a text, **Speak** (also on Enter), **Interrupt**, and one line per utterance: how many characters, how much audio, when the first audio came and, on the default player, when playback started.

## Testing

`tests/examples/test_setup_demo.py`: a session speaks and reports what it said; an utterance in progress is interrupted; a refused config fails the start and frees its loop; `start` builds the engine from the saved section; the demo's default config passes the checks and starts through the app's model; the page builds with its Session content.

The page was also run live: the checks over a kokoro config, a session started, an utterance spoken, a test sentence, the session stopped; and the piper extra, uninstalled, installed back by the page's model through uv and seen ready without a restart.

## Open questions

1. **Streaming text** (`say_stream`) is not offered by the Session tab.
2. **A voice audition** inside the demo (several voices, side by side) is the job of `tts-engine-audition` ([voice-audition.md](voice-audition.md)) for now.
