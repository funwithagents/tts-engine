# Setting the engine up from an application

`tts_engine.setup` tells an application everything it needs to let a person choose and configure a voice, before any engine is built: what the config looks like, what must be installed, what must be downloaded, and whether it works. Every function takes or returns plain JSON-compatible data, so it can sit behind a form, an HTTP endpoint or a command line.

All of them work on the **engine block**, the object `TTSEngineConfig.from_dict` takes ([configuration.md](configuration.md)).

```python
from tts_engine import setup

config = {"module": {"type": "kokoro", "voice": "af_heart"}}

setup.validate(config)       # [] when the engine accepts this config
setup.requirements(config)   # ["tts-engine[kokoro]"]
setup.check(config)          # the model files, each "ready", "missing" or "unknown"
setup.prepare(config)        # downloads what is missing
setup.probe(config)          # speaks one sentence: {"ok": True, "message": ..., "duration_s": ...}
```

## The functions

| Function | What it gives | Needs |
|---|---|---|
| `describe()` | The engine block as a JSON Schema: one branch per module, each field with its type, default, bounds and a description | Nothing |
| `requirements(config)` | The extra the selected module needs, as `tts-engine[<extra>]`, or `[]` | Nothing |
| `validate(config)` | Every reason the engine would refuse the config, each with a JSON Pointer to the field and the message the engine raises | Nothing |
| `check(config)` | The files the config needs on the machine (model weights, voices) and whether each is there | The module's extra |
| `prepare(config, on_progress)` | Downloads the missing files; `on_progress` receives `{"id", "fraction", "message"}` | The extra, the network |
| `options(name, config)` | The choices of `"voices"` (the selected module's catalog) or `"output-devices"`, each with the config fields it sets | The extra; a key for a hosted catalog |
| `probe(config)` | Builds the engine, speaks one sentence on the configured device, closes it. Never raises | Everything the config needs |

"Nothing" means the base install: `describe`, `requirements` and `validate` import no provider library, read no key and use no network, so an application can show and check a config for a module that is not installed yet.

## What `validate` does and does not catch

It applies the rules the engine and the module apply to the config's shape: types, ranges, required fields, fields that exclude each other. A config it accepts can still fail when the engine is built, for what only the provider knows: a voice name that does not exist, a key that is rejected, a file that is not there. `probe` is the way to find those out before a real use.

## The schema's annotations

Beyond standard JSON Schema keywords, a field may carry:

- `x-advanced`: a tuning field most people leave alone;
- `x-secret-env`: the field holds the *name* of an environment variable that contains a key;
- `x-secret`: the field holds a key itself, and should not be displayed;
- `x-options`: the name to pass to `options` for a list of choices.

## Local models

Only the local modules have files to check. `phonon` reports its model as `unknown`: its runtime chooses the files while loading, so they cannot be listed beforehand, and its `prepare` loads the model once to fetch them.
