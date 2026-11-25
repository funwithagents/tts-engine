---
code:
  - src/tts_engine/setup.py
  - src/tts_engine/modules/fields.py
  - src/tts_engine/modules/base.py
tests:
  - tests/test_setup.py
  - tests/modules/test_fields.py
---

# Setup

**Status:** Implemented

## Purpose

Everything an application needs to set the engine up for a user, without knowing any backend: which modules exist, what each one's config looks like, which extra a config needs, what it needs on the machine (model weights), and which voices and output devices it can choose from. An application draws a form from it, installs what is missing, and only then builds a `TTSEngine`.

Today this knowledge is spread over each module's constructor, the per-module docs, and the error raised when something is missing. A config can only be checked by constructing the module, which imports its provider library, reads its key and loads its model. This spec makes the same knowledge available as data, before anything is installed or loaded.

These functions implement the **module contract** defined by the `app-setup` project (its `specs/module-contract.md`), version 1. That spec is the reference for the shapes exchanged; this one says what tts-engine puts in them. The engine does not depend on `app-setup` at runtime: the contract is plain data, and `app-setup` is only a dev dependency, for the conformance check its tests run.

It adds no behaviour to the engine: a config that works today works the same, and the constructors raise the same messages.

## The `setup` module

`tts_engine.setup` exposes seven functions over the **engine block** — the document `TTSEngineConfig.from_dict` takes ([configuration.md](configuration.md)):

```python
def config_schema() -> dict: ...
def required_extras(config: dict) -> list[str]: ...
def check_config(config: dict) -> list[dict]: ...
def check_setup(config: dict, *, base_dir: str | None = None) -> list[dict]: ...
def prepare(
    config: dict,
    item: str,
    *,
    on_progress: Callable[[dict], None] | None = None,
    base_dir: str | None = None,
) -> None: ...
def options(name: str, config: dict, *, base_dir: str | None = None) -> list[dict]: ...
def probe(config: dict, *, base_dir: str | None = None) -> dict: ...
```

**Relative paths.** The four functions that look at the machine take `base_dir`, the folder a relative path of the config is relative to (a voice file, a Piper model, an `audiofile` entry), and read the module block as the loader gives it to the module, `TTSEngineConfig.from_dict(config, base_dir=base_dir)`: an app passes its folder, the one it also builds its engine against, so Check, Prepare, Test and the engine find the same file. `None` means the working directory.

They take and return JSON-compatible data only, are synchronous, and import nothing outside the package at module level. The first four run on the base install, with no network: `config_schema`, `required_extras` and `check_config` read nothing but the config, and `check_setup` also tries the module's imports and reads the environment. `prepare` and `options` need the module's extra; `prepare`, `options` and `probe` may use the network.

Each is the engine's own part (the module choice, `player`, `gain_db`) plus the method of the same name on the selected module ("On a module" below).

| Function | Returns |
|---|---|
| `config_schema()` | `{"contract": 1, "distribution": "tts-engine", "title": "Text to speech", "schema": …}` — the engine block as a JSON Schema (below). The same value on every call |
| `required_extras(config)` | The extras this config needs, as requirement strings on this distribution: `["tts-engine[phonon]"]`, or `[]`. Reads `module.type` only, never raises, and returns `[]` for an unknown or missing type |
| `check_config(config)` | What is wrong in the config itself: the problems that make the engine refuse it, each `{"path": "/module/voice_id", "message": …}` with a JSON Pointer into the engine block; `[]` when it is accepted |
| `check_setup(config)` | What this machine is missing to run it: the module's extra, the keys its config names, its files; each `{"id", "title", "status": "ready"/"missing"/"unknown", "detail", "preparable", "required"}`; `[]` when it needs none |
| `prepare(config, item)` | Makes **one** preparable item ready (downloads one file), an id `check_setup` returned. Blocking, idempotent; another item may be prepared at the same time on another thread. An id that is not a preparable item of the config raises `ValueError` naming it; a failure raises `TTSError`. `on_progress` receives `{"fraction", "message"}` about that item |
| `options(name, config)` | The choices of a named list, each `{"label": …, "fields": {…}}` — the fields to merge into the object the list belongs to. `"voices"` and `"output-devices"` |
| `probe(config)` | Checks the config for real as lightly as possible (a hosted module's voice list, a local model loaded and released) and reports `{"ok", "message", "duration_s"}`. Never synthesizes, never raises |

## Fields are declared once

A module declares its config fields as data, and both its constructor and `check_config` read them, so the schema and the validation cannot drift apart.

`modules/fields.py`:

```python
@dataclass(frozen=True)
class Field:
    name: str
    kind: FieldKind | tuple[FieldKind, ...]   # "string", "number", "integer", "boolean", "objects"
    description: str
    default: Any = None
    implied: Any = None
    required: bool = False
    nullable: bool = True
    choices: tuple[Any, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    allow_empty: bool = False
    advanced: bool = False
    secret: Literal["env", "literal"] | None = None
    options: str | None = None
    message: str | None = None


def read_fields(config: dict, fields: Sequence[Field], *, label: str, error: type[Exception] = ValueError) -> dict[str, Any]: ...
def field_problems(config: dict, fields: Sequence[Field], *, label: str) -> list[tuple[str, str]]: ...
def fields_schema(fields: Sequence[Field]) -> dict: ...
```

- **`kind`** is one kind, or several for a field that accepts more than one (piper's `speaker`: a name or an index from 0); bounds then apply to its numeric values. `"objects"` is a list of objects (audiofile's `files`): only its being a list is checked here, its content is the module's `config_rules` to validate, and a form edits it as raw JSON.
- **`default`** is the value an absent field takes, and it changes how `null` is read: without one, an explicit `null` counts as absent; with one, it is rejected like any other wrong value. This is the policy the shared helpers already had.
- **`nullable=False`** refuses an explicit `null` on a field that has no default and may be absent (pocket's `voice`).
- **`implied`** is for a field the module falls back on in code (`voice or _DEFAULT_VOICE`) or leaves to the provider: the value a form shows as the default, never used to validate, so `null` stays accepted.
- **`allow_empty`** lets a string be empty, for a field where an empty value means something of its own.
- **`message`** replaces the default wording of an invalid value, for the fields whose message predates this spec (`"ElevenLabs module requires a non-empty 'voice_id'"`).
- **`read_fields`** returns each field's value (its default when absent) or raises `error` on the first invalid one; a number comes back as a `float`. Its rules and wording are those of the shared helpers ([tts-module-interface.md](tts-module-interface.md), "Shared config helpers"): a present string is non-empty, a number is finite and within its bounds, a bool is never a number, an integer is never a float. `optional_str`, `finite_number` and `positive_int` are now thin wrappers over it; the constructors no longer call them, and `list_voices` still does, since it reads a config that may lack the required fields.
- **`field_problems`** runs the same rules and returns every failure as `(field name, message)` instead of raising on the first.
- **`fields_schema`** returns the `properties` and `required` of a JSON Schema object: for each field its `type` (with `null` when it has no default and is not required), `description`, `default` (the default, else the implied value), `enum`, the bounds, and the annotations `x-advanced`, `x-secret-env` (`secret="env"`), `x-secret` (`secret="literal"`, which no module declares any more: a key is never written in a config) and `x-options`.

The file uses the standard library only and knows nothing about speech: the error type is the caller's.

### On a module

`TTSModule` gains these class-level members:

```python
class TTSModule(ABC):
    kind: ClassVar[ModuleKind]
    title: ClassVar[str]                      # "ElevenLabs", "Phonon"
    extra: ClassVar[str | None]               # "elevenlabs"; None for the fixtures
    fields: ClassVar[tuple[Field, ...]]

    # The contract, over the module block, each with a default:
    @classmethod
    def required_extras(cls, config: dict) -> list[str]: ...
    @classmethod
    def check_config(cls, config: dict) -> list[tuple[str, str]]: ...
    @classmethod
    def check_setup(cls, config: dict) -> list[dict]: ...
    @classmethod
    def prepare(cls, config: dict, item: str, on_progress: ProgressCallback | None = None) -> None: ...
    @classmethod
    def options(cls, name: str, config: dict) -> list[dict]: ...

    # What they are built on:
    @classmethod
    def imports(cls) -> None: ...
    @classmethod
    def check_files(cls, config: dict) -> list[dict]: ...
    @classmethod
    def fetch(cls, config: dict, item: dict, report: Report) -> None: ...
    @classmethod
    def validated(cls, config: dict) -> dict[str, Any]: ...
    @classmethod
    def fields_for(cls, config: dict) -> tuple[Field, ...]: ...
    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None: ...
```

The module's methods carry the contract's names, so `tts_engine.setup.check_setup` is the engine's part plus the selected module's `check_setup`, and so on. Each has a default; a module overrides only what it needs. In practice:

| Member | Default | Overridden by |
|---|---|---|
| `title`, `extra`, `fields` | none: declared by every module, like `kind` | every module |
| `required_extras` | `tts-engine[<extra>]`, `[]` for a fixture | none |
| `check_config` | the problems of `fields_for(config)`, or, when there are none, the `config_rules` failure under the name `""` | none |
| `check_setup` | the extra (from `imports`), then the keys, then `check_files` once the extra imports | none |
| `prepare` | the extra's imports (its install hint), the item looked up in `check_files` (`ValueError` when it is not a preparable one), `fetch` unless it is ready, then `1.0` | none |
| `options` | `"voices"` from `list_voices`, each `{"label": "River (en, neutral)", "fields": VoiceInfo.config}`; `[]` for any other name | none |
| `imports` | nothing | every module with an extra |
| `check_files` | `[]` | the five local modules |
| `fetch` | `ValueError`: nothing to prepare | the five local modules |
| `config_rules` | nothing | the modules with a rule across fields or on a field's content |
| `fields_for` | `fields` | deepgram |

- **`title`, `extra`, `fields`** have no default, like `kind`. The label of a module's messages is `"<title> module"`. `extra` replaced the name-to-extra rule `tests/test_extras.py` derived by pattern; the test now compares each module's `extra` with `pyproject.toml`. That every registered module declares them is checked by `tests/test_setup.py`, not at class creation: a test or an application may write a stub module for the engine that never goes through `setup`.
- **`validated`** is `read_fields` over `fields_for(config)`, then `config_rules`. **It is the first statement of every constructor, before any import**, so a shape error is reported whether or not the extra is installed, and what `check_config` accepts is what the constructor accepts.
- **`imports`** holds the provider imports of the module's extra, in a `try` raising `ConfigError` with the install hint. The constructor calls it before its own imports of the same names, so the hint has one home; `check_setup` calls it to find out whether the extra is usable. Trying the imports, rather than looking for the packages, tests what will run: a broken install, a version missing a symbol, a missing native library all show.
- **`fields_for`** returns `fields`, unless a field's rule depends on another's value: deepgram's `speed` range and its `expressivity` follow the model family, so it returns them narrowed for an Aura model. A form is drawn from `fields`; a config is validated against `fields_for`.
- **`config_rules`** is the module's hook for a rule a `Field` cannot state: across fields (kokoro's language following its voice) or on a field's content (an `hf://` reference's form). It raises `ConfigError` and looks at the config only: no provider import, no file, no network.

`api_key_env` (`secret="env"`) is the one key field of every hosted module, declared by `key_env_field`: not `required`, since it has a default, the provider's usual variable (`GRADIUM_API_KEY`); `openai-speech-compatible` has none, for a compatible server that needs no key. Whether that variable is set is a machine fact, reported by `check_setup` and at construction, not a shape rule. There is no `api_key` field: a key written in a config is refused by `check_config` (under the pointer `/api_key`) and at construction, so a config never holds a secret ([elevenlabs-module.md](modules/elevenlabs.md), "API key resolution").

`base_dir` is not a field: it is the reserved key the loader injects ([configuration.md](configuration.md)).

## The schema

`config_schema()["schema"]` is the engine block:

- **`module`** — an object with one `oneOf` branch per registered module, in registry order. Each branch has the module's `title` (suffixed with its kind: "Phonon (local)"), `type` as a `const`, and `fields_schema(cls.fields)`. Its `required` list is `type` plus the module's required fields. A renamed type ([tts-module-interface.md](tts-module-interface.md), "Module names and renamed types") has no branch: the schema offers current names only, and `check_config` still accepts the old one.
- **`player`** — `device` (a string, an integer or `null`; `x-options: "output-devices"`) and `discard` (boolean, default `false`, advanced).
- **`gain_db`** — a number from −60 to 24, default `0`, advanced.

The fixture modules (`tone`, `audiofile`) are in the schema: they need nothing and let a user hear the pipeline work before any install.

## `check_config`

It reports, in this order:

1. the engine-level rules of [configuration.md](configuration.md) — `module` an object with a `type`, `gain_db`'s range, `player`'s types, `discard` excluding a device — each at its own path;
2. an unknown module type, at `/module/type`;
3. the selected module's `check_config`, each problem at `/module/<field>` (or `/module` for a `config_rules` rule).

The engine-level problems are all reported, each check being independent; the module's follow.

It constructs nothing. A config it accepts can still be refused later for what only the provider's library or service knows: an unknown voice name or language, a key the provider rejects, a file that does not exist. Those surface when the engine is built, as today.

Unknown keys in a module block are accepted, as the constructors accept them today.

## `check_setup` and `prepare`

`check_setup` returns, in this order:

1. **The extra** — one item, `extra:tts-engine[<extra>]`, never preparable, always required: `ready` ("Installed.") when the module's `imports` succeed, else `missing` with the install hint the constructor raises. A fixture has none.
2. **The keys** — one item per environment variable a `secret="env"` field names, or names by default when it is absent (`key:<VARIABLE>`), never preparable, always required: `ready` when it is set and non-empty, else `missing` with a sentence naming the variable and the field. Whether the provider accepts the key is `probe`'s business.
3. **The files** — only once the extra imports, since looking them up uses the provider's library: the module's `check_files`.

The environment read is the process's own: an application keeping keys in a file loads them first.

Only the local modules have files, one item per file or model a config selects:

| Module | Item | How `check_files` knows |
|---|---|---|
| `kokoro`, `piper` | The model and voice files of the pinned Hub repository | The Hugging Face cache, read without network |
| `qwen3` | The talker and codec files of its quantization | The library's own offline lookup, then the Hugging Face cache per file |
| `pocket` | The weights, the tokenizer and the voice of a packaged model | The library's own config naming each file, looked up in its caches; a custom `config` is one `unknown` item |
| `phonon` | The model, and the voice when one is set | `unknown` for anything fetched from the Hub: its runtime picks the files inside the load, by device and quantization, so the set cannot be told from the config. A local path is `ready` or `missing` |

A config pointing at local files has an item per file, `ready` or `missing`, never `preparable` and always `required`: the detail says which path is absent.

**`required`.** Every item carries it. The extra, the keys and a local file the config names are `required: true`: without them the engine cannot be built. Every file fetched from the Hub (or, for `pocket`, from a URL) is `required: false`: the constructor fetches a missing one itself, through the same call `prepare` makes (kokoro's and piper's `hf_hub_download`, qwen3's and pocket's model load, phonon's runtime inside the load), so a session can start without it, only slower and with the network. Every preparable item of the engine is such a file, and every file it cannot fetch is one the config names: here `required` is exactly `not preparable`.

`prepare(config, item)` makes **one** item ready: the extra's imports first (a missing extra raises its install hint), then the item looked up among `check_files`' — an id that is not a preparable item of this config (an extra, a key, a local file, a typo) raises `ValueError` naming it. An item already `ready` returns at once. Otherwise the module's `fetch` gets it through the call the constructor makes, so the constructor then finds it in place:

| Module | `fetch` of one item | Progress |
|---|---|---|
| `kokoro`, `piper` | `hf_hub_download` of that file | Measured |
| `qwen3` | `hf_hub_download` of that file (the one `resolve_gguf_paths` makes for it); when the library does not say its file names, its own `resolve_gguf_paths`, both files at once | Measured; unmeasured for the library's own download |
| `pocket` | An `hf://` file through `hf_hub_download` (what the library's `download_if_necessary` calls), an `https://` one through `download_if_necessary`; the weights' candidates in order, the public weights standing in for the gated ones; a model whose files cannot be named is loaded once and released | Measured for the Hub; unmeasured for a URL or a load |
| `phonon` | The model loaded as the constructor does, with the voice for the voice item and without it for the model item, then released: its runtime has no download-only call, and its items stay `unknown`, so each call loads | Unmeasured |

**Concurrency.** app-setup prepares the items of a config at the same time, one thread each: two calls for two different items must both succeed, neither relying on the other. Two Hub downloads of two files run side by side (the Hub locks each file it writes). `phonon`'s two items both load the model: its `fetch` holds a lock of the module, so the loads run one after the other — the second finds the model's files fetched by the first — and two models are never held in memory at once.

**Progress.** `on_progress` receives `{"fraction", "message"}` about that item only (no `id`: the caller knows which item it asked for): once as it starts, as it advances, and `1.0` once ready (`"<title> ready"`). A Hub download is measured: `hf_hub_download` takes a `tqdm_class`, and `prepare` hands it a small stand-in for `tqdm` (not a subclass, which the Hub would disable without a terminal) that turns the bytes written against the file's size into a fraction, about once per percent — `"Downloading kokoro-v1.0.onnx: 120 MB of 310 MB"`; a Xet download, whose written bytes may come in one flush at the end, is measured by the larger of the bytes received and the bytes written. What has no hook — `phonon`'s runtime, written in Rust, pocket's URL download, a model load — reports `fraction: None` with what it is doing, then `1.0`. Hosted and fixture modules have no items and nothing to prepare.

## `options`

- **`"voices"`** — the selected module's `list_voices(config["module"])` ([tts-module-interface.md](tts-module-interface.md), "Voice listing"), each voice as `{"label": "<name> (<languages>, <gender>)", "fields": <VoiceInfo.config>}`. Declared on each module branch, since a voice can set several fields at once. `VoiceListingUnsupported` and `ConfigError` propagate: the caller shows the message and leaves the fields as free text.
- **`"output-devices"`** — the machine's output devices, each `{"label": "<name>", "fields": {"device": "<name>"}}`, preceded by the system default (`{"device": null}`).

An unknown name returns `[]`.

## `probe`

`probe(config)` checks the config for real, as lightly as possible, and returns `{"ok", "message", "duration_s"}`. It never raises, never synthesizes, and opens no device.

- First the module's own `check_config`: a config it refuses fails the probe with its first problem.
- **A hosted module** makes one authenticated, free call: its voice list (`list_voices`), with the key the config names (`api_key_env` or its default) and its endpoint. When the config selects a voice (the list's `VoiceInfo.config` fields, such as `voice_id`), it must be in that list: a voice outside the account fails the probe. A hosted module with no voice list (Gemini, OpenAI Realtime, an OpenAI-compatible server) is built and closed instead, and the message says the key is checked at the first utterance.
- **A local module** is built, which loads its model, and closed, which releases it: the extra, the files and the compute device are proven.
- **The fixture** is built and closed.
- Not checked: the output part (`player`, `gain_db`): nothing is played, so a wrong device shows at the first utterance.
- It runs on a thread of its own, so it can be called from a thread that already runs an event loop.
- `ok: false` carries the message of what failed, which already names what to do for a missing extra or an unset key variable.

## Testing

`tests/modules/test_fields.py`:

- `read_fields` and `field_problems` agree: for each kind and each rule (empty string, `NaN`, a bool as a number, a float as an integer, out of bounds, `null` with and without a default, a value outside `choices`), the first raises exactly the message the second returns.
- The messages are the ones the shared helpers raise today.
- `fields_schema` emits each attribute and annotation.

`tests/test_setup.py`:

- Every registered module declares `title`, `extra` and `fields`, and every field has a description.
- `config_schema()` is stable across calls and has one branch per registered module.
- Every example config under `examples/configs/` (an engine block each) passes `check_config`, and uses only keys the schema names.
- For each module, a config that `check_config` refuses on a field is refused by the constructor with the same message, and the reverse — run with the provider library present, the key set to a dummy and the network unused (the constructor is stopped at its first import or load).
- `config_schema`, `check_config` and `required_extras` import no provider library: run in a subprocess with every provider import blocked. In the same subprocess, every module with an extra reports that extra `missing` in `check_setup`, with the hint its `imports` raises, and nothing else.
- `required_extras` returns `tts-engine[<extra>]` for each module, `[]` for the fixtures, for `{}` and for an unknown type, and every extra it names is declared in `pyproject.toml`.
- `check_setup` reports each installed extra `ready`; a key variable `missing` until it is set (empty counts as unset), and no key item when the config holds the key itself.
- Every item of every module carries the contract's six keys, and only a preparable item is not required.
- `check_files` on `kokoro` reports `missing`, preparable and not required, then `ready` around a fake cache; `prepare` of one item downloads that file only, through the call the constructor uses, with progress measured against its size, and does nothing when it is ready.
- Two items prepared at the same time on two threads both succeed (each download waits for the other to start).
- An id that is not a preparable item (a file the module does not have, the extra, a key, a local file) raises `ValueError` naming it; a failed download raises `TTSError` with the cause.
- `options("voices", …)` over a fixture module's catalog, and the propagation of `VoiceListingUnsupported`.
- `probe` over the `tone` fixture and a recording sink: it reports `ok`, audio reached the sink, and the engine was closed. With a module that fails to build, it reports `ok: false` with the engine's message and raises nothing.

The contract's conformance check runs over the same examples.

## Open questions

1. **`phonon`'s items stay `unknown`.** Its always-needed files (`config.json`, `tokenizer.json`) could at least prove a model was never fetched; a full answer needs the runtime to expose which files a config selects.
2. **`check_setup` imports the providers**: `pocket`'s pulls torch, which takes about a second the first time. It loads no model.
3. **Deprecated fields and renamed types** are accepted and not offered. Whether `check_config` should report them as warnings (a third kind of result) is deferred.
4. **The demo app** — a page with a Setup panel over these functions and a Speak tab that builds the engine and speaks — is a separate spec ([gradio-demo.md](gradio-demo.md)).
5. **`phonon`'s progress is unmeasured.** Its runtime downloads inside the load with no hook, and which files (hence how many bytes) it fetches is its own choice; the Hugging Face cache's growth could be watched once the runtime says what it selects.
