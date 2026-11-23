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

A page to try the engine without writing a config by hand: choose a voice, configure it, install its extra, download its model, then hear it. It is the engine's demo and the first app built on [setup.md](setup.md): its Setup and Secrets panels, the app's folder and the checks behind them come from the `module-setup` project, whose specs are the reference for them; this spec says what the engine's demo puts around them: its section, its page and its sessions.

## Running it

```
uv run python -m examples.setup_demo [--folder PATH] [--host ADDRESS] [--port N] [--start]
```

The page opens on its Setup tab with the `tone` fixture, which needs nothing installed; with `--start`, a session is started from the saved config at launch, and the page opens on the Session tab when it did. The app's folder (its saved config and its `.env`) is `~/.config/tts-engine-demo` unless `--folder` names another. The page listens on `127.0.0.1` unless `--host` names another address.

`module-setup[ui]` (and through it gradio) is the `demo` dependency group, synced by default with `dev`: it is never a dependency of the published package.

## What the demo declares

- **One section, `tts`**, whose document is the engine block (`TTSEngineConfig.from_dict`'s), described by `tts_engine.setup`.
- **The host is `tts-engine` itself**, so "Install missing extras" installs this project's own extras (`phonon`, `kokoro`, …) from its lock.
- **The default config** is `{"tts": {"module": {"type": "tone"}}}`.
- **The page** is the demo's own: three tabs, **Session** first, then module-setup's Setup panel and its Secrets panel, which follows the Setup panel's draft (a key a newly chosen voice needs appears before saving).
- **The session** is a `SpeechSession`: a `TTSEngine` built from the saved section, a relative path in it resolved against the app's folder, running on an event loop of its own so the page's handlers can wait for it. `stop` interrupts what is being said, closes the engine (its model, its device) and ends the loop. A config the engine refuses fails the start, its message on the page, and leaves no thread behind.
- **`Sessions`** holds at most one session: **Start** reads the saved config, refuses with the model's blockers (the config's problems, what the machine is missing), else builds the session from the `tts` section; **Stop** closes it. The model is locked while a session starts, runs or stops, so the Setup panel cannot save under it. Leaving the page's process stops it.
- **The Session tab** has **Start** and **Stop**, the session's state (refreshed every second), why a start was refused, a text, **Speak** (also on Enter), **Interrupt**, and one line per utterance: how many characters, how much audio, when the first audio came and, on the default player, when playback started.

## Testing

`tests/examples/test_setup_demo.py`: a session speaks and reports what it said; an utterance in progress is interrupted; a config the engine refuses fails and frees its loop; a session is the engine built from the saved section; one starts from the saved config and locks it until stopped; a config with problems does not start; the demo's default config is ready; the page puts its Session tab first, then the two panels, and opens on Setup.

The page was also run live, then again once it was built from module-setup's panels: the checks over a kokoro config, a session started, an utterance spoken, a test sentence, the session stopped; and the piper extra, uninstalled, installed back by the page's model through uv and seen ready without a restart. It was then driven in a headless browser: Kokoro chosen, a voice picked from its list (54 voices), the output switched to `discard`, saved, a session started, a sentence spoken (first audio after about 200 ms), stopped.

## Open questions

1. **Streaming text** (`say_stream`) is not offered by the Session tab.
2. **A voice audition** inside the demo (several voices, side by side) is the job of `tts-engine-audition` ([voice-audition.md](voice-audition.md)) for now.
