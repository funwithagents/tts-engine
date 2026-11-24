---
code:
  - examples/gradio_demo/__init__.py
  - examples/gradio_demo/__main__.py
  - examples/gradio_demo/app.py
  - examples/gradio_demo/default_config.json
  - examples/gradio_demo/session.py
  - pyproject.toml
tests:
  - tests/examples/test_gradio_demo.py
---

# Gradio demo

**Status:** Implemented

## Purpose

A page to try the engine without writing a config by hand: choose a voice, configure it, install its extra, download its model, then hear it. It is the engine's demo and the first app built on [setup.md](setup.md): its Setup and Secrets panels, the app's folder and the checks behind them come from the `app-setup` project, whose specs are the reference for them; this spec says what the engine's demo puts around them: its section, its page and its voice.

## Running it

```
uv run python -m examples.gradio_demo [--folder PATH] [--host ADDRESS] [--port N]
```

The page opens on its Setup tab with the Phonon voice (`Juniper`), a local model: on a fresh install its section says what it needs: its extra (required, the Install button) and its model files (not required, the Prepare button: the engine would fetch them itself at the first utterance, slower). The app's folder (its saved config and its `.env`) is `~/.config/tts-engine-demo` unless `--folder` names another. The page listens on `127.0.0.1` unless `--host` names another address.

`app-setup[ui]` (and through it gradio) is the `demo` dependency group, synced by default with `dev`: it is never a dependency of the published package.

## What the demo declares

- **One section, `tts`**, whose document is the engine block (`TTSEngineConfig.from_dict`'s), described by `tts_engine.setup`.
- **The host is `tts-engine` itself**, so "Install missing extras" installs this project's own extras (`phonon`, `kokoro`, …) from its lock.
- **The default config** is a committed file, `examples/gradio_demo/default_config.json` (`{"tts": {"module": {"type": "phonon", "voice": "Juniper"}}}`): what app-setup writes into an empty folder on first launch.
- **The page** is the demo's own: three tabs, **Speak** first, then app-setup's Setup panel and its Secrets panel, which follows the Setup panel's draft (a key a newly chosen voice needs appears before saving).
- **A `SpeechSession`** is a `TTSEngine` built from the saved section, a relative path in it resolved against the app's folder, running on an event loop of its own so the page's handlers can wait for it. `stop` interrupts what is being said, closes the engine (its model, its device) and ends the loop. A config the engine refuses fails the build and leaves no thread behind.
- **The `Voice`** is the session the page speaks with, built when it is needed: nothing runs between two utterances, so there is no Start, no Stop and no lock on the config. Each utterance reads the saved config: with its blockers (the config's problems, the required items the machine is missing: an extra, a key, a local file), it says why instead of speaking — a model file not prepared yet does not block, the build fetches it; else it reuses the session built from the same `tts` section, or closes it and builds one from the new section. A save, from any browser tab or by hand in the file, is heard at the next utterance. One utterance at a time: a second waits, and the session is only replaced between two. A failed build is said on the page. Leaving the page's process closes it.
- **The Speak tab** has a text, **Speak** (also on Enter), **Interrupt**, and one line per utterance: how many characters, how much audio, when the first audio came and, on the default player, when playback started; or why the saved config cannot speak yet.

## Testing

`tests/examples/test_gradio_demo.py`: a session speaks and reports what it said; an utterance in progress is interrupted; a config the engine refuses fails and frees its loop; a session is the engine built from the saved section; the voice is built at the first utterance and kept while the saved config holds, rebuilt from a saved change at the next one, the config never locked; a config with problems says why and builds nothing; the demo's default config is the committed file, valid, and written into an empty folder; the page puts its Speak tab first, then the two panels, and opens on Setup.

The page was also run live, then again once it was built from app-setup's panels: the checks over a kokoro config, a session started, an utterance spoken, a test sentence, the session stopped; and the piper extra, uninstalled, installed back by the page's model through uv and seen ready without a restart. It was then driven in a headless browser: Kokoro chosen, a voice picked from its list (54 voices), the output switched to `discard`, saved, a session started, a sentence spoken (first audio after about 200 ms), stopped.

## Open questions

1. **Streaming text** (`say_stream`) is not offered by the Speak tab.
2. **A voice audition** inside the demo (several voices, side by side) is the job of `tts-engine-audition` ([voice-audition.md](voice-audition.md)) for now.
