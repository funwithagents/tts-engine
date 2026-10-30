# Voice audition — hear candidate voices side by side and pick some

Choosing a voice means hearing candidates from several backends say the same sentence, and knowing how long each one makes the listener wait. `tts-engine-audition` builds a candidate list from the backends' own catalogs (`list_voices`), renders every candidate through `TTSEngine` while measuring its time to first audio and its level, and writes a static page to listen to them one by one and mark each Yes, Maybe or No. Design, file format and cache rules: [specs/voice-audition.md](../specs/voice-audition.md).

The command is installed with the package and needs no extra of its own: each candidate needs its module's extra, and one whose extra or key is missing is skipped.

## Running it

Hosted backends read their key from the environment (`CARTESIA_API_KEY`, …).

```bash
# 1. An audition file from the catalogs: English male or neutral voices, 8 per backend
tts-engine-audition new my-assistant.json \
    --text "Hello! What would you like to talk about today?" \
    --module phonon --module cartesia --module gradium \
    --gender male --gender neutral --language en --limit 8 --keep-unknown

# 2. Render every candidate (one warm-up, then 3 measured runs) and write the page
tts-engine-audition render my-assistant.json

# 3. Listen and choose
open my-assistant.audition/index.html
```

From a checkout of this repository, prefix the commands with `uv run`. The keys of this repository's maintainer live in `~/.zshrc`, which a non-interactive shell does not read: `zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run tts-engine-audition render …'`, as for `tests-e2e`.

| Command | What it does | Options |
|---|---|---|
| `new OUT.json` | Lists each module's voices and writes an audition file; refuses to overwrite without `--force` | `--text` (required), `--module` (a type, or a JSON module block for another key variable, a `model` or an endpoint; repeatable), `--gender`, `--language` (repeatable), `--query`, `--limit` (per module), `--keep-unknown`, `--name`, `--force` |
| `render AUDITION.json` | Renders the candidates into `<audition>.audition/` (`index.html`, `results.json`, `samples/<id>.wav`) | `--runs` (measured runs after the warm-up, default 3), `--only ID…`, `--out`, `--force`, `--target` (dBFS the suggested `gain_db` aims at, default −22), `--choices FILE` |
| `page AUDITION.json` | Rebuilds `index.html` from the existing results, rendering nothing | `--out`, `--target`, `--choices FILE` |

`--log-level LEVEL`, before the command, sets the log level (default `WARNING`).

Good to know:

- **The audition file is plain JSON** (a `text` and candidates, each an `id` and a complete module `config`, plus an optional `description` shown on the page), so it can be written or edited by hand; `new` is only a starting point.
- **Rendering again only renders what changed** (the text or a candidate's config); `--force` re-measures everything.
- **A missing key or extra skips a candidate**, it does not fail the pass. A catalog without gender or language metadata (phonon, pocket, piper, qwen3) only passes those filters with `--keep-unknown`. Gemini, OpenAI Realtime and `openai-compatible` cannot list voices: write their candidates by hand.
- **Hosted renders are paid calls**: one warm-up plus `--runs` syntheses of the sentence per candidate.
- **The latency shown is this machine's**, three runs on one sentence after a warm-up. It is not comparable to the controlled numbers of the [latency benchmark](../benchmarks/README.md#results) and is not meant to be quoted.
- **The level column** is the RMS of each sample's active speech (silences left out), with the engine `gain_db` that brings the voice to the target and the peak after it, flagged when it would clip. It is `tts_engine.level`'s measure, the one `tts-engine-level` uses. Change the target on the page; put the chosen value in the engine block next to the voice ([configuration.md](configuration.md#output-gain)).
- **Choices stay in the browser** (`localStorage`); export them as `<name>-choices.json` to keep them, share them or hand them to an agent, and import them on another machine. `--choices FILE` embeds such a file as the page's starting choices, for a browser that has none stored: a page you publish opens with your decision.
- **Rendered folders (`*.audition/`) are git-ignored** in this repository; an `api_key` in a config is never shown on the page.

## Examples

The example auditions are in [`examples/auditions/`](../examples/auditions/), in the repository, not in the package.

### `demo.json`

[`demo.json`](../examples/auditions/demo.json) has two or three hand-picked conversational voices per backend: `tts-engine-audition render examples/auditions/demo.json` is the quickest way to hear them all.

### `reachy-mini.json`

[`reachy-mini.json`](../examples/auditions/reachy-mini.json) is the audition that chose the voices of the Reachy Mini robot, driven through `reachy-mini-bridge`. Its goal: an English voice for a small conversational robot, male or gender-neutral, young, possibly a touch robotic. 58 candidates across ten modules; the choices exported after listening, [`reachy-mini-choices.json`](../examples/auditions/reachy-mini-choices.json), are 10 Yes, 12 Maybe and 36 No.

**Hear it:** <https://funwithagents.github.io/tts-engine/auditions/reachy-mini/>. The page opens on those choices.

**Render it yourself**, from the repository root:

```bash
tts-engine-audition render examples/auditions/reachy-mini.json \
    --choices examples/auditions/reachy-mini-choices.json
```

- It needs the keys of the hosted backends (Cartesia, ElevenLabs, Gradium, Deepgram, Mistral) and the extras of the local ones (phonon, pocket, piper, qwen3, kokoro); what is missing is skipped.
- The kokoro candidates use the int8 export of Kokoro-82M, which is not on the Hugging Face Hub: download `kokoro-v1.0.int8.onnx` (114 MB) from the `model-files-v1.1` release of [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx/releases) into `benchmarks/models/`, where their `model` path points (the latency benchmark expects it there too).
- Without `--choices`, open the page and use **Import choices** with `reachy-mini-choices.json` to load the decision.
