# Setting the engine up from an application

`tts_engine.setup` tells an application everything it needs to let a person choose and configure a voice, before any engine is built: what the config looks like, what must be installed, what must be downloaded, and whether it works. Every function takes or returns plain JSON-compatible data, so it can sit behind a form, an HTTP endpoint or a command line.

All of them work on the **engine block**, the object `TTSEngineConfig.from_dict` takes ([configuration.md](configuration.md)).

```python
from tts_engine import setup

config = {"module": {"type": "kokoro", "voice": "af_heart"}}

setup.check_config(config)      # [] when the engine accepts this config
setup.required_extras(config)   # ["tts-engine[kokoro]"]
setup.check_setup(config)       # the extra, then the model files: each "ready", "missing" or "unknown"
setup.prepare(config, "kokoro:model", on_progress=print)   # downloads one missing file
setup.probe(config)             # a light real check: {"ok": True, "message": ..., "duration_s": ...}
```

## Try it: the demo page

```
uv run python -m examples.setup_demo
```

from a checkout opens a page built on these functions: choose a voice, fill its fields, install its extra and download its model with a click, then hear it. Its folder (saved config, keys) is `~/.config/tts-engine-demo` unless `--folder` names another.

## The functions

| Function | What it gives | Needs |
|---|---|---|
| `config_schema()` | The engine block as a JSON Schema: one branch per module, each field with its type, default, bounds and a description | Nothing |
| `required_extras(config)` | The extra the selected module needs, as `tts-engine[<extra>]`, or `[]` | Nothing |
| `check_config(config)` | What is wrong in the config itself: every reason the engine would refuse it, each with a JSON Pointer to the field and the message the engine raises | Nothing |
| `check_setup(config)` | What this machine is missing to run the config: the module's extra, the environment variables holding its keys, its files (model weights, voices); each item says whether it is `preparable` and `required` | Nothing |
| `prepare(config, item)` | Downloads one file, an item `check_setup` listed; `on_progress` receives `{"fraction", "message"}` about it. Several items can be prepared at the same time, one thread each | The extra, the network |
| `options(name, config)` | The choices of `"voices"` (the selected module's catalog) or `"output-devices"`, each with the config fields it sets | The extra; a key for a hosted catalog |
| `probe(config)` | A light real check: a hosted module lists its voices with the key (and finds the configured voice there); a local module loads its model and releases it. Nothing is played. Never raises | Everything the config needs |

"Nothing" means the base install, with no network: `config_schema`, `required_extras` and `check_config` look at the config only, so an application can show and check a config for a module that is not installed yet. `check_setup` also tries the module's imports (a missing extra is one of its answers) and reads the environment.

The usual order is `check_config`, then `check_setup`; install what it says is missing and set the keys; `prepare` each preparable item; `check_setup` again; `probe` when you want to hear it.

## `check_config` and `check_setup`

`check_config` looks at the config: types, ranges, required fields, fields that exclude each other. Its answer is the same on every machine. `check_setup` looks at this machine: is the extra installed, is each key's environment variable set, are the files there. A config both accept can still fail when the engine is built, for what only the provider knows: a voice name that does not exist, a key that is rejected. `probe` is the way to find those out before a real use.

## The schema's annotations

Beyond standard JSON Schema keywords, a field may carry:

- `x-advanced`: a tuning field most people leave alone;
- `x-secret-env`: the field holds the *name* of an environment variable that contains a key;
- `x-secret`: the field holds a key itself, and should not be displayed;
- `x-options`: the name to pass to `options` for a list of choices.

## Local models

Only the local modules have files to check. A file the module downloads from the Hugging Face Hub is `preparable` and not `required`: the engine fetches a missing one itself when it is built, so it starts anyway, only slower; preparing it beforehand avoids the wait. A local file the config names is `required` and not preparable: only you can put it there.

A Hub download reports its progress against the file's size ("Downloading kokoro-v1.0.onnx: 120 MB of 310 MB"). `phonon` reports its model as `unknown`: its runtime chooses the files while loading, so they cannot be listed beforehand, and its `prepare` loads the model once to fetch them, with no measure of how far it is.
