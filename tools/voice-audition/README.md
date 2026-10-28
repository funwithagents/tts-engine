# Voice audition — hear candidate voices side by side and pick some

Choosing a voice means hearing candidates from several backends say the same sentence, and knowing how long each one makes the listener wait. `audition.py` builds a candidate list from the backends' own catalogs (`list_voices`), renders every candidate through `TTSEngine` while measuring its time to first audio, and writes a static page to listen to them one by one and mark each Yes, Maybe or No. Design, file format and cache rules: [specs/voice-audition.md](../../specs/voice-audition.md).

It is tooling run from a checkout: not installed with the package, never run by CI.

## Running it

From the repository root. Hosted backends read their key from the environment; the keys live in `~/.zshrc`, so source it (as for `tests-e2e`).

```bash
# 1. An audition file from the catalogs: English male or neutral voices, 8 per backend
uv run python tools/voice-audition/audition.py new my-assistant.json \
    --text "Hello! What would you like to talk about today?" \
    --module phonon --module cartesia --module gradium \
    --gender male --gender neutral --language en --limit 8 --keep-unknown

# 2. Render every candidate (one warm-up, then 3 measured runs) and write the page
zsh -ic 'source ~/.zshrc >/dev/null 2>&1; uv run python tools/voice-audition/audition.py render my-assistant.json'

# 3. Listen and choose
open my-assistant.audition/index.html
```

The hand-picked [`examples/demo.json`](examples/demo.json) has two or three conversational voices per backend: `render tools/voice-audition/examples/demo.json` is the quickest way to hear them all.

| Command | What it does | Options |
|---|---|---|
| `new OUT.json` | Lists each module's voices and writes an audition file; refuses to overwrite without `--force` | `--text` (required), `--module` (a type, or a JSON module block for another key variable, a `model` or an endpoint; repeatable), `--gender`, `--language` (repeatable), `--query`, `--limit` (per module), `--keep-unknown`, `--name`, `--force` |
| `render AUDITION.json` | Renders the candidates into `<audition>.audition/` (`index.html`, `results.json`, `samples/<id>.wav`) | `--runs` (measured runs after the warm-up, default 3), `--only ID…`, `--out`, `--force` |
| `page AUDITION.json` | Rebuilds `index.html` from the existing results, rendering nothing | `--out` |

Good to know:

- **The audition file is plain JSON** (a `text` and candidates, each an `id` and a complete module `config`), so it can be written or edited by hand; `new` is only a starting point.
- **Rendering again only renders what changed** (the text or a candidate's config); `--force` re-measures everything.
- **A missing key or extra skips a candidate**, it does not fail the pass. A catalog without gender or language metadata (phonon, pocket, piper, qwen3) only passes those filters with `--keep-unknown`. Gemini, OpenAI Realtime and `openai-compatible` cannot list voices: write their candidates by hand.
- **Hosted renders are paid calls**: one warm-up plus `--runs` syntheses of the sentence per candidate.
- **The latency shown is this machine's**, three runs on one sentence after a warm-up. It is not comparable to the controlled numbers of the [latency benchmark](../../benchmarks/latency/RESULTS.md) and is not meant to be quoted.
- **Choices stay in the browser** (`localStorage`); export them as `<name>-choices.json` to keep them, share them or hand them to an agent, and import them on another machine.
- **Rendered folders (`*.audition/`) are git-ignored**; an `api_key` in a config is never shown on the page.
