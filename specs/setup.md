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

These functions implement the **module contract** defined by the `module-setup` project (its `specs/module-contract.md`), version 1. That spec is the reference for the shapes exchanged; this one says what tts-engine puts in them. The engine does not depend on `module-setup` at runtime: the contract is plain data, and `module-setup` is only a dev dependency, for the conformance check its tests run.

It adds no behaviour to the engine: a config that works today works the same, and the constructors raise the same messages.

## The `setup` module

`tts_engine.setup` exposes seven functions over the **engine block** — the document `TTSEngineConfig.from_dict` takes ([configuration.md](configuration.md)):

```python
def config_schema() -> dict: ...
def required_extras(config: dict) -> list[str]: ...
def check_config(config: dict) -> list[dict]: ...
def check_setup(config: dict) -> list[dict]: ...
def prepare(
    config: dict, on_progress: Callable[[dict], None] | None = None
) -> None: ...
def options(name: str, config: dict) -> list[dict]: ...
def probe(config: dict) -> dict: ...
```

They take and return JSON-compatible data only, are synchronous, and import nothing outside the package at module level. The first four run on the base install, with no network: `config_schema`, `required_extras` and `check_config` read nothing but the config, and `check_setup` also tries the module's imports and reads the environment. `prepare` and `options` need the module's extra; `prepare`, `options` and `probe` may use the network.

Each is the engine's own part (the module choice, `player`, `gain_db`) plus the method of the same name on the selected module ("On a module" below).

| Function | Returns |
|---|---|
| `config_schema()` | `{"contract": 1, "distribution": "tts-engine", "title": "Text to speech", "schema": …}` — the engine block as a JSON Schema (below). The same value on every call |
| `required_extras(config)` | The extras this config needs, as requirement strings on this distribution: `["tts-engine[phonon]"]`, or `[]`. Reads `module.type` only, never raises, and returns `[]` for an unknown or missing type |
| `check_config(config)` | What is wrong in the config itself: the problems that make the engine refuse it, each `{"path": "/module/voice_id", "message": …}` with a JSON Pointer into the engine block; `[]` when it is accepted |
| `check_setup(config)` | What this machine is missing to run it: the module's extra, the keys its config names, its files; each `{"id", "title", "status": "ready"/"missing"/"unknown", "detail", "preparable"}`; `[]` when it needs none |
| `prepare(config, on_progress)` | Makes every preparable item ready (downloads the weights). Blocking, idempotent, raises `TTSError` on failure. `on_progress` receives `{"id", "fraction", "message"}` |
| `options(name, config)` | The choices of a named list, each `{"label": …, "fields": {…}}` — the fields to merge into the object the list belongs to. `"voices"` and `"output-devices"` |
| `probe(config)` | Speaks one test sentence with this config and reports `{"ok", "message", "duration_s"}`. Never raises |

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
- **`allow_empty`** lets a string be empty (`api_key`, where an empty literal means "use `api_key_env`").
- **`message`** replaces the default wording of an invalid value, for the fields whose message predates this spec (`"ElevenLabs module requires a non-empty 'voice_id'"`).
- **`read_fields`** returns each field's value (its default when absent) or raises `error` on the first invalid one; a number comes back as a `float`. Its rules and wording are those of the shared helpers ([tts-module-interface.md](tts-module-interface.md), "Shared config helpers"): a present string is non-empty, a number is finite and within its bounds, a bool is never a number, an integer is never a float. `optional_str`, `finite_number` and `positive_int` are now thin wrappers over it; the constructors no longer call them, and `list_voices` still does, since it reads a config that may lack the required fields.
- **`field_problems`** runs the same rules and returns every failure as `(field name, message)` instead of raising on the first.
- **`fields_schema`** returns the `properties` and `required` of a JSON Schema object: for each field its `type` (with `null` when it has no default and is not required), `description`, `default` (the default, else the implied value), `enum`, the bounds, and the annotations `x-advanced`, `x-secret-env` (`secret="env"`), `x-secret` (`secret="literal"`) and `x-options`.

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
    def prepare(cls, config: dict, on_progress: ProgressCallback | None = None) -> None: ...
    @classmethod
    def options(cls, name: str, config: dict) -> list[dict]: ...

    # What they are built on:
    @classmethod
    def imports(cls) -> None: ...
    @classmethod
    def check_files(cls, config: dict) -> list[dict]: ...
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
| `prepare` | nothing | the five local modules |
| `options` | `"voices"` from `list_voices`, each `{"label": "River (en, neutral)", "fields": VoiceInfo.config}`; `[]` for any other name | none |
| `imports` | nothing | every module with an extra |
| `check_files` | `[]` | the five local modules |
| `config_rules` | nothing | the modules with a rule across fields or on a field's content |
| `fields_for` | `fields` | deepgram |

- **`title`, `extra`, `fields`** have no default, like `kind`. The label of a module's messages is `"<title> module"`. `extra` replaced the name-to-extra rule `tests/test_extras.py` derived by pattern; the test now compares each module's `extra` with `pyproject.toml`. That every registered module declares them is checked by `tests/test_setup.py`, not at class creation: a test or an application may write a stub module for the engine that never goes through `setup`.
- **`validated`** is `read_fields` over `fields_for(config)`, then `config_rules`. **It is the first statement of every constructor, before any import**, so a shape error is reported whether or not the extra is installed, and what `check_config` accepts is what the constructor accepts.
- **`imports`** holds the provider imports of the module's extra, in a `try` raising `ConfigError` with the install hint. The constructor calls it before its own imports of the same names, so the hint has one home; `check_setup` calls it to find out whether the extra is usable. Trying the imports, rather than looking for the packages, tests what will run: a broken install, a version missing a symbol, a missing native library all show.
- **`fields_for`** returns `fields`, unless a field's rule depends on another's value: deepgram's `speed` range and its `expressivity` follow the model family, so it returns them narrowed for an Aura model. A form is drawn from `fields`; a config is validated against `fields_for`.
- **`config_rules`** is the module's hook for a rule a `Field` cannot state: across fields (kokoro's language following its voice) or on a field's content (an `hf://` reference's form). It raises `ConfigError` and looks at the config only: no provider import, no file, no network.

`api_key` (`secret="literal"`) and `api_key_env` (`secret="env"`) are two declared fields of every hosted module. Neither is `required`: needing a key at all is a machine fact `resolve_api_key` reports at construction, not a shape rule.

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

1. **The extra** — one item, `extra:tts-engine[<extra>]`, never preparable: `ready` ("Installed.") when the module's `imports` succeed, else `missing` with the install hint the constructor raises. A fixture has none.
2. **The keys** — one item per environment variable a `secret="env"` field names (`key:<VARIABLE>`), never preparable: `ready` when it is set and non-empty, else `missing` with a sentence naming the variable and the field. A config holding the key itself (`api_key`) has none. Whether the provider accepts the key is `probe`'s business.
3. **The files** — only once the extra imports, since looking them up uses the provider's library: the module's `check_files`.

The environment read is the process's own: an application keeping keys in a file loads them first.

Only the local modules have files, one item per file or model a config selects:

| Module | Item | How `check_files` knows |
|---|---|---|
| `kokoro`, `piper` | The model and voice files of the pinned Hub repository | The Hugging Face cache, read without network |
| `qwen3` | The talker and codec files of its quantization | The library's own offline lookup, then the Hugging Face cache per file |
| `pocket` | The weights, the tokenizer and the voice of a packaged model | The library's own config naming each file, looked up in its caches; a custom `config` is one `unknown` item |
| `phonon` | The model, and the voice when one is set | `unknown` for anything fetched from the Hub: its runtime picks the files inside the load, by device and quantization, so the set cannot be told from the config. A local path is `ready` or `missing` |

A config pointing at local files has an item per file, `ready` or `missing`, never `preparable`: the detail says which path is absent.

`prepare` fetches what `check_files` reports missing and preparable, through the same calls the constructor makes, so the constructor then finds everything in place. `phonon`'s runtime has no download-only call: its `prepare` loads the model and releases it, each time it is asked, since its items never become `ready`. Hosted and fixture modules have no items and nothing to prepare.

## `options`

- **`"voices"`** — the selected module's `list_voices(config["module"])` ([tts-module-interface.md](tts-module-interface.md), "Voice listing"), each voice as `{"label": "<name> (<languages>, <gender>)", "fields": <VoiceInfo.config>}`. Declared on each module branch, since a voice can set several fields at once. `VoiceListingUnsupported` and `ConfigError` propagate: the caller shows the message and leaves the fields as free text.
- **`"output-devices"`** — the machine's output devices, each `{"label": "<name>", "fields": {"device": "<name>"}}`, preceded by the system default (`{"device": null}`).

An unknown name returns `[]`.

## `probe`

`probe(config, *, sink=None)` builds a `TTSEngine` from the config, speaks one short sentence through it, closes it, and returns `{"ok", "message", "duration_s"}`. It never raises.

- It speaks on the device the config names, so the user hears the voice they chose. With `player.discard`, or with a `sink` given by a caller that has its own audio path, the audio is synthesized without a device and the message says nothing was played.
- It runs on a thread of its own, so it can be called from a thread that already runs an event loop.
- The sentence is a fixed English one. A module whose config selects a language the sentence does not fit still proves the key, the model and the device.
- `ok: false` carries the message of the error the engine raised, which already names what to do for a missing extra or an unset key variable.
- `duration_s` covers the build and the speech together.

## Testing

`tests/modules/test_fields.py`:

- `read_fields` and `field_problems` agree: for each kind and each rule (empty string, `NaN`, a bool as a number, a float as an integer, out of bounds, `null` with and without a default, a value outside `choices`), the first raises exactly the message the second returns.
- The messages are the ones the shared helpers raise today.
- `fields_schema` emits each attribute and annotation.

`tests/test_setup.py`:

- Every registered module declares `title`, `extra` and `fields`, and every field has a description.
- `config_schema()` is stable across calls and has one branch per registered module.
- Every example config under `examples/` passes `check_config`, and uses only keys the schema names.
- For each module, a config that `check_config` refuses on a field is refused by the constructor with the same message, and the reverse — run with the provider library present, the key set to a dummy and the network unused (the constructor is stopped at its first import or load).
- `config_schema`, `check_config` and `required_extras` import no provider library: run in a subprocess with every provider import blocked. In the same subprocess, every module with an extra reports that extra `missing` in `check_setup`, with the hint its `imports` raises, and nothing else.
- `required_extras` returns `tts-engine[<extra>]` for each module, `[]` for the fixtures, for `{}` and for an unknown type, and every extra it names is declared in `pyproject.toml`.
- `check_setup` reports each installed extra `ready`; a key variable `missing` until it is set (empty counts as unset), and no key item when the config holds the key itself.
- `check_files` on `kokoro` reports `missing` then `ready` around a fake cache; `prepare` calls the download the constructor uses, and does nothing when everything is ready.
- `options("voices", …)` over a fixture module's catalog, and the propagation of `VoiceListingUnsupported`.
- `probe` over the `tone` fixture and a recording sink: it reports `ok`, audio reached the sink, and the engine was closed. With a module that fails to build, it reports `ok: false` with the engine's message and raises nothing.

The contract's conformance check runs over the same examples.

## Open questions

1. **`phonon`'s items stay `unknown`.** Its always-needed files (`config.json`, `tokenizer.json`) could at least prove a model was never fetched; a full answer needs the runtime to expose which files a config selects.
2. **`check_setup` imports the providers**: `pocket`'s pulls torch, which takes about a second the first time. It loads no model.
3. **Deprecated fields and renamed types** are accepted and not offered. Whether `check_config` should report them as warnings (a third kind of result) is deferred.
4. **The demo app** — a page with a Setup tab over these functions and a Session tab that builds the engine and speaks — is a separate spec.
