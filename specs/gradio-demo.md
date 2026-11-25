---
code:
  - examples/gradio_demo/__init__.py
  - examples/gradio_demo/__main__.py
  - examples/gradio_demo/app.py
  - examples/gradio_demo/default_config.json
  - examples/gradio_demo/session.py
  - examples/config_app/__init__.py
  - examples/config_app/__main__.py
  - pyproject.toml
tests:
  - tests/examples/test_gradio_demo.py
---

# Gradio demo

**Status:** Implemented

## Purpose

A page to try the engine without writing a config by hand: choose a voice, configure it, install its extra, download its model, then hear it. It is the engine's demo and the first app built on [setup.md](setup.md): its Setup and Secrets panels, the app's folder, a config file as the config's source, and the checks behind them come from the `app-setup` project, whose specs are the reference for them; this spec says what the engine's demo puts around them: its section, its page, its voice, its launches and its config app.

## Running it

The demo launches three ways, all from a checkout:

| Launch | The config | The Setup tab |
|---|---|---|
| `uv run python -m examples.gradio_demo [--folder DIR]` | The demo's folder: `~/.config/tts-engine-demo` unless `--folder` names another; its `config.json`, saved by the Setup tab | Editable |
| `uv run python -m examples.gradio_demo --config FILE` | `FILE`, an engine block (any of `examples/configs/`), spoken with as it is: never written | Read-only |
| `uv run python -m examples.config_app FILE` | `FILE`, edited and saved back to it ("The config app" below) | Editable; no Speak tab |

`--folder` and `--config` are one or the other. The demo also takes `--host` (the page listens on `127.0.0.1` unless it names another address) and `--port`; the config app takes the same two.

**Keys.** In each launch, the keys (a hosted voice's) come from the `.env` next to the config: the folder's, or the one in the file's directory; `--env PATH` names another. The shell's own variables win over the file. One `.env` next to `examples/configs/` serves every example; the repository's `.gitignore` lists `.env`, so it is never committed.

**From the folder**, the page opens on its Setup tab with the Phonon voice (`Juniper`), a local model: on a fresh install its section says what it needs: its extra (required, the Install button) and its model files (not required, the Prepare button: the engine would fetch them itself at the first utterance, slower).

**From a file**, the demo has no session to start (below): `--config` means "speak with this file". The Setup tab shows the file's form read-only, with its checks and their actions (Check, Install, Prepare, Test), which never change the config; there is no Save. Speak reads the file at each utterance, so a hand edit is heard at the next one. A missing file, or one that is not a JSON object, stops the demo at launch with an error naming it, before any page.

**A folder from an earlier version** holds its config wrapped in a `tts` key (`{"tts": {…}}`), the shape before the demo's config became the engine block itself: delete the folder once (`rm -r ~/.config/tts-engine-demo`) and the demo writes its default again.

`app-setup[ui]` (and through it gradio) is the `demo` dependency group, synced by default with `dev`: it is never a dependency of the published package.

## What the demo declares

- **One section, `tts`**, whose document is the engine block (`TTSEngineConfig.from_dict`'s), described by `tts_engine.setup`. **The app is bare** (app-setup's `App(bare=True)`): its folder's `config.json`, a `--config` file, the config app's file and its default config are the engine block itself, with no `tts` key around it, so the engine's example configs run in the demo as they are, and a config saved by the demo loads with `TTSEngineConfig.from_json_file`. The panels still see `{"tts": …}`: app-setup wraps and unwraps it.
- **The host is `tts-engine` itself**, so "Install missing extras" installs this project's own extras (`phonon`, `kokoro`, …) from its lock.
- **The default config** is a committed file, `examples/gradio_demo/default_config.json` (`{"module": {"type": "phonon", "voice": "Juniper"}}`): what app-setup writes into an empty folder on first launch.
- **The page** is the demo's own: three tabs, **Speak** first, then app-setup's Setup panel and its Secrets panel, which follows the Setup panel's draft (a key a newly chosen voice needs appears before saving). While the Setup tab's draft differs from the saved config, the Speak tab says so: "The Setup tab has unsaved changes: Speak uses the saved config." (app-setup's `AppModel.unsaved`, the line its `unsaved_notice` gives next to a Start button, worded for Speak.)
- **A `SpeechSession`** is a `TTSEngine` built from the saved section, a relative path in it resolved against the config's directory (the app's folder, or the file's directory), running on an event loop of its own so the page's handlers can wait for it. `stop` interrupts what is being said, closes the engine (its model, its device) and ends the loop. A config the engine refuses fails the build and leaves no thread behind.
- **The `Voice`** is the session the page speaks with, built when it is needed: nothing runs between two utterances, so there is no Start, no Stop and no lock on the config. Each utterance reads the saved config: with its blockers (the config's problems, the required items the machine is missing: an extra, a key, a local file), it says why instead of speaking — a model file not prepared yet does not block, the build fetches it; else it reuses the session built from the same `tts` section, or closes it and builds one from the new section. A save, from any browser tab or by hand in the file (the folder's `config.json`, or a `--config` file), is heard at the next utterance. One utterance at a time: a second waits, and the session is only replaced between two. A failed build is said on the page. Leaving the page's process closes it.
- **The Speak tab** has a text, **Speak** (also on Enter), **Interrupt**, and one line per utterance: how many characters, how much audio, when the first audio came and, on the default player, when playback started; or why the saved config cannot speak yet.

## The config app

`examples/config_app/` edits one engine config file with the demo's Setup and Secrets panels and runs nothing: no Speak tab, no engine.

```
uv run python -m examples.config_app FILE [--env PATH] [--host ADDRESS] [--port N]
```

It is a few lines over app-setup: the demo's `APP` (the same section, bare), `add_config_app_arguments`, `open_config_app` and `config_page`. Save writes the engine block back to `FILE`; Revert reads it again; there is no Reset, since a file has no default. The file must exist: a missing one stops the app with an error naming it. It runs in this project's environment (`uv run`), so its checks and what it installs are those of the environment the config will run in. It is not a console script: the published package never depends on app-setup.

## Testing

`tests/examples/test_gradio_demo.py`: a session speaks and reports what it said; an utterance in progress is interrupted; a config the engine refuses fails and frees its loop; a session is the engine built from the saved section; the voice is built at the first utterance and kept while the saved config holds, rebuilt from a saved change at the next one, the config never locked; a config with problems says why and builds nothing; the demo's default config is the committed file (the engine block, unwrapped), valid, and written into an empty folder; the page puts its Speak tab first, then the two panels, and opens on Setup. The launches: from the folder by default, holding the engine block, its `.env` in it; from a file, spoken with (a hand edit heard at the next utterance), never written, its `.env` next to it or `--env`'s, the Setup tab without Save, Revert or Reset but with its checks; a missing file refused at launch with its name; an example config run as it is; the config app saving the engine block alone back to its file, with Revert and no Reset, and refusing a missing file. `tests/test_setup.py` runs `check_config` over every `examples/configs/*.json`.

The page was also run live, then again once it was built from app-setup's panels: the checks over a kokoro config, a session started, an utterance spoken, a test sentence, the session stopped; and the piper extra, uninstalled, installed back by the page's model through uv and seen ready without a restart. It was then driven in a headless browser: Kokoro chosen, a voice picked from its list (54 voices), the output switched to `discard`, saved, a session started, a sentence spoken (first audio after about 200 ms), stopped.

## Open questions

1. **Streaming text** (`say_stream`) is not offered by the Speak tab.
2. **A voice audition** inside the demo (several voices, side by side) is the job of `tts-engine-audition` ([voice-audition.md](voice-audition.md)) for now.
