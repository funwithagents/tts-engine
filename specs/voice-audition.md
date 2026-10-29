---
code:
  - tools/voice-audition/audition.py
  - tools/voice-audition/page.html
  - tools/voice-audition/examples/demo.json
tests:
  - tests/test_voice_audition.py
---

# Voice Audition

**Status:** Implemented

## Purpose

Choosing a voice for an application (an assistant, a character, a device) means hearing candidates side by side, across backends, saying the same sentence, and knowing how long each one makes the listener wait. Doing that by hand means one script per backend and a folder of WAV files. The voice audition tool in [`tools/voice-audition/`](../tools/voice-audition/) does it from one JSON file:
- it builds a candidate list from the backends' own catalogs ([tts-module-interface.md](tts-module-interface.md), "Voice listing");
- it renders every candidate through `TTSEngine`, measuring its time to first audio and its level;
- it writes a page to listen to them one by one and mark each Yes, Maybe or No. The Yes and Maybe voices get their own tabs, the choices can be exported, and each voice shows the `gain_db` that brings it to a common level.

Like the latency benchmark ([benchmark.md](benchmark.md)), it is tooling, not part of the library: nothing in `src/` depends on it, it is not installed with the package, and CI never runs it (it needs keys and makes paid calls). It is run from a checkout.

## Usage

From the repository root, with the provider keys in the environment:

```bash
# 1. An audition file from the catalogs: English male or neutral voices, 8 per backend
uv run python tools/voice-audition/audition.py new my-assistant.json \
    --text "Hello! What would you like to talk about today?" \
    --module phonon --module pocket --module cartesia --module gradium \
    --gender male --gender neutral --language en --limit 8

# 2. Render every candidate (3 measured runs each) and write the page
uv run python tools/voice-audition/audition.py render my-assistant.json

# 3. Open my-assistant.audition/index.html; after editing the file, re-render: only what changed is rendered again
```

`page` rebuilds `index.html` from the audition file and the existing results without rendering anything. The audition file can also be written or edited by hand: `new` is a starting point, not a requirement. [`examples/demo.json`](../tools/voice-audition/examples/demo.json) is a hand-picked one, a male conversational voice per backend.

## The audition file

```json
{
  "name": "my-assistant",
  "text": "Hello! What would you like to talk about today?",
  "candidates": [
    {
      "id": "cartesia-zeke",
      "label": "Zeke - Friendly Sidekick",
      "section": "cartesia",
      "notes": "male · young adult · en · High pitched, friendly",
      "config": {"type": "cartesia", "api_key_env": "CARTESIA_API_KEY", "model": "sonic-3.6",
                 "voice_id": "e00d0e4c-a5c8-443f-a8a3-473eb9a62355"}
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | no (default: the file's stem) | Names the audition on the page and keys its stored choices, so two auditions never share them |
| `text` | yes | The one sentence every candidate says; a non-empty string |
| `candidates[].id` | yes | Unique within the file, `[a-z0-9][a-z0-9._-]*`: the WAV's file name and the key of its choice and result |
| `candidates[].config` | yes | A complete `engine.module` block (`type` included), passed to `TTSEngine` as is: any field the module accepts |
| `candidates[].label` | no (default: the id) | What the page shows as the voice |
| `candidates[].section` | no (default: `config.type`) | The page groups candidates by section, in order of first appearance |
| `candidates[].notes` | no | Free text shown next to the voice |

Unknown keys are rejected so a typo is not silently ignored. A relative file path in a `config` (a pocket `.wav`, a kokoro model) resolves against the audition file's directory, as `TTSEngineConfig.from_json_file` does for a config file.

**Secrets.** A config may carry a literal `api_key`; the file is the user's. The page never shows one: displayed configs replace `api_key` with `"***"`. `results.json` holds no config at all.

## `new`: candidates from the catalogs

`new OUT.json --text TEXT --module M [--module M …] [filters]` lists each module's voices with `list_voices` and writes an audition file. It refuses to overwrite an existing file without `--force`.

- **`--module`** is a module type or a JSON module block. A bare type gets `{"type": T}`, plus `"api_key_env": "<T>_API_KEY"` (upper-cased, `-` → `_`) when its `kind` is `hosted`, the providers' own convention. A JSON block is used as is, to pass another key variable, a `model` (qwen3's speakers, Deepgram's family) or an endpoint. The block becomes the base of every candidate from that module, so the fields `list_voices` reads (credentials, model) are also the ones the engine is built with.
- **Filters**, all optional and combined with AND:
  - `--gender G` (repeatable; `male`, `female`, `neutral`);
  - `--language L` (repeatable; matches a voice whose language code is `L` or starts with `L-`, comparing case-insensitively with `_` read as `-`, so `en` matches `en`, `en-US`, `en_GB`);
  - `--query Q` (case-insensitive substring of name, description or tags);
  - `--limit N` (the first N matches per module, in the catalog's order).
- **Unknown metadata.** A voice whose catalog gives no gender (or no language) fails a gender (or language) filter, since nothing says it matches. `--keep-unknown` keeps such voices instead, which is what makes phonon, pocket, piper or qwen3 voices pass a gender filter.
- **Candidates.** Each voice gives one candidate:
  - `id`: `<type>-<slug of the voice name>`, with `-2`, `-3`… on a clash;
  - `label`: the voice name;
  - `section`: the type;
  - `notes`: gender, age, languages, description and tags, joined with ` · `;
  - `config`: the module's base block merged with the voice's `config`.
- **A module that cannot list.** `VoiceListingUnsupported`, a `ConfigError` (missing extra, unset key) or a `TTSError` is printed with the module's type, and `new` goes on with the others. It exits non-zero only when no module yielded a voice.

## `render`: samples and latency

`render AUDITION.json [--out DIR] [--runs N] [--only ID …] [--force] [--target DBFS]` renders the candidates into `DIR` (default: the audition file's path with `.json` replaced by `.audition`, e.g. `my-assistant.audition/`):

```
my-assistant.audition/
  index.html          # the page (always rewritten at the end)
  results.json        # one entry per candidate id
  samples/<id>.wav    # 16-bit mono PCM at the module's rate
```

**Per candidate, in file order, one at a time in this process:**

1. **Skip unchanged.** The candidate's cache key is a hash of `text` and `config`. If `results.json` has an `ok` entry with the same key and the WAV exists, the candidate is not rendered again, unless `--force` is given. An unchanged entry written before levels were recorded gets them measured from its WAV, with no synthesis. `--only` restricts the pass to the given ids; the others keep their results.
2. **Skip what cannot run here.** A `config` whose `api_key_env` names an unset variable is recorded `skipped` with that reason, before any construction. So is a construction failing because the module's extra is not installed (its `ConfigError` names the extra). Any other construction failure is an `error`.
3. **Render and measure.** The engine is built with a capturing sink. One warm-up `say(text)` runs first: it pays the connection, model load and kernel compilation, which an application pays once. Then come `--runs` measured `say(text)` calls (default 3, at least 1), each timed from the call to the first PCM chunk the sink receives. The audio of the last run is written as the sample. The engine is closed before the next candidate.
4. **Record** in `results.json`:
   - `ok` entries: `key`, `ttfa_ms` (every measured run), `ttfa_median_ms`, `duration_s`, `sample_rate`, `level_dbfs` and `peak_dbfs` (below), `rendered_at`;
   - `skipped` entries: `key` and `reason`;
   - `error` entries: `key` and `error` (the exception's type and message).

   A failing synthesis is an `error` and the pass goes on. `results.json` is written after every candidate, so an interrupted pass keeps what it finished.

**Latency is measured, not looked up.** The page shows the median and the range of the TTFA measured during the render, on this machine and network, after warm-up. That is the number a listener of this machine's application would get, independent of any benchmark result. Three runs keep the cost of a hosted voice at four syntheses of one sentence; `--runs` raises it when the numbers need to be firmer.

## Level and gain

Each provider, model and voice speaks at its own level, so voices compared side by side, or played one after another by an application, jump in volume at every change. The engine's `gain_db` ([configuration.md](configuration.md), "`engine.gain_db`") corrects that per engine; the audition is where its value is found, since choosing a voice and its gain then happen in one place.

- **`level_dbfs` and `peak_dbfs`** are `tts_engine.level.measure_level` on the kept sample (the last run), at the module's own rate: the RMS of the active speech (20 ms frames above −40 dBFS) and the largest absolute sample, in dBFS to 0.1 dB, `null` when there is none ([level.md](level.md), "The measure"). The tool calls the library's measure, so its levels and those of `tts-engine-calibrate` are the same number for the same audio. It measures the audio it already rendered, with no extra synthesis.
- The level of a configuration is stable across texts, so one sentence gives a usable value. `tts-engine-calibrate` measures three sentences, for a firmer one on the voice finally kept.
- **The target.** The page shows, under each level, the **suggested `gain_db`** and the **peak after that gain**, flagged when it passes 0 dBFS, since the engine would then clip: `suggest_gain`'s arithmetic ([level.md](level.md), "The suggestion"), redone in the page's script so the target stays editable. The target starts at `--target` (default `DEFAULT_TARGET_DBFS`, **−22 dBFS**, on `render` and `page`), and changing it on the page recomputes every row. A target below most voices keeps the gains negative, and attenuating never clips.

`render` exits non-zero when a candidate ends in `error`, after writing everything else; `skipped` is not a failure. Entries of ids no longer in the file are dropped from `results.json`, and their WAVs are left alone.

## The page

`index.html` is `page.html` with the audition's data embedded as JSON: name, text, target level, sections, candidates (label, notes, redacted config) and results. A static page with no server: it opens from disk, and the WAVs are referenced relatively.

- **Rows.** One table with fixed column widths, so every row has the same columns in the same place. Each section starts with a heading row, and each candidate is one row:
  - the label;
  - the notes;
  - the TTFA (median, with min–max over the runs on hover);
  - the level, the suggested `gain_db` and the peak after it (see "Level and gain");
  - an audio player, or the reason the candidate was skipped or failed;
  - four stacked radio buttons, **Yes**, **Maybe**, **No** and **NotSet** (the default);
  - the module config.

  A row marked Yes or Maybe gets a coloured edge, a row marked No is dimmed.
- **Tabs.** **All**, **Yes (n)**, **Maybe (n)**. The Yes and Maybe tabs show only those candidates, under their section headings (a section with none left is hidden), with live counts.
- **Listening one by one.** Starting a player pauses the others. When a sample ends, the next one *visible in the current tab* starts and scrolls into view (a checkbox turns this off), so a tab can be heard straight through.
- **Choices** live in the browser's `localStorage` under `voice-audition:<name>`. They survive a reload and a re-render, but stay in that browser only, so the page exports and imports them:
  - **Export** downloads `<name>-choices.json`: `{"audition": name, "yes": [ids], "maybe": [ids], "no": [ids]}`.
  - **Import** reads such a file and replaces the stored choices with it. Ids no longer in the audition are ignored, and a file whose `audition` names another audition is refused (a file with no `audition` key is accepted). The `yes`/`maybe`/`no` lists alone are the whole format, so a hand-written list works too.

The exported file is how choices leave the browser: for another machine, a colleague, or an agent asked to turn the Yes voices into configs.

## Error handling

- An invalid audition file (missing or empty `text`, missing, duplicate or malformed ids, a candidate without a `config` object or `type`, unknown keys) stops the command before any synthesis, with a message naming the file and the field.
- `new` and `render` never stop on one backend's failure; what failed is printed and recorded, and the exit code reports it (above).
- Nothing is printed of a key: a `skipped` reason names the variable, never its value.

## Tests

`tests/test_voice_audition.py`, in the fast tier, loads `audition.py` by path like `tests/test_benchmark.py` does, with no network and no key:
- the file validation;
- `new` against a faked `list_voices`: filters, unknown metadata, limit, ids and their deduplication, the hosted key variable, a module that cannot list, refusing to overwrite;
- `render` with the `tone` fixture: the WAV and its rate, the runs and their median, the level and peak, the cache key (an unchanged candidate not rendered again, a changed text rendering it again, `--force`), an older entry's level filled in from its WAV, a candidate with an unset key `skipped` without construction, a bad config an `error`;
- the page: its data embedded, `api_key` redacted, the storage key, the target.

The page's script is not run by the tests.

## Open questions

Deferrals only:

1. **Several sentences.** One sentence covers a first pass; a greeting, a long answer and a question would separate close voices better. That would make `text` a list with labels, give each candidate one sample per text, and key the cache by both.
2. **From choices back to a file.** A `select` command could turn an exported choices file into a new audition with only the Yes (and Maybe) candidates, to re-render a shortlist with more runs or a longer text, or print their configs.
3. **Rendering with a gain.** A candidate could carry its `gain_db` so the page plays the voices already levelled; for now the samples are the engine's native level and the page only suggests the value.
4. **Packaged CLI.** If users of the library outside this repository need it, the tool can become a `tts-engine-audition` entry point with `page.html` as package data. That makes its file format a public interface, which is why it starts in the repository.
