# Contributing

Thanks for your interest in `tts-engine`. This page says how the project is run today, so you know what to expect before you spend time on it.

## How the project is run

`tts-engine` is developed by one person, in the open, as a spec-driven project: every change starts as a design in [specs/](specs/), becomes an implementation plan in [plans/](plans/), and only then code. [AGENTS.md](AGENTS.md) describes that workflow and the checks every change passes. The public API is not expected to change much; the backends follow their providers, whose models and SDKs move often.

## Pull requests: not for now

External pull requests are not accepted for now. This is not a judgement on contributions: every change here goes through a spec, a plan and the live tier against paid provider accounts, and reviewing an outside change to that standard takes the time that would otherwise go into the project itself. A pull request will be closed with thanks and, when the idea is a good one, turned into an issue so it is not lost.

This file will say so when that changes.

## Feedback: yes, please

Issues are the way in, at [github.com/funwithagents/tts-engine/issues](https://github.com/funwithagents/tts-engine/issues), and every kind of feedback is welcome:

- **Bug reports.** Say what you ran: the module and its config with secrets removed, `say` or `say_stream`, the OS and Python version, the version of the provider's package; what you expected, what happened, and the log output. A small script that reproduces it is the best possible report.
- **Reports from setups the author does not test on.** Development happens on an Apple-silicon Mac, and CI plays audio only on Linux, into a null device. A report that playback works, or does not, on Windows, Linux arm64 or an Intel Mac, or that a provider behaves differently on a paid tier or in another region, is especially valuable, even when everything went fine.
- **Backend requests.** A provider or local model you would like to see as a module, and why: its latency, its voices, its license, its platform.
- **API and design feedback.** What you would want the engine, a module's config or the MCP server to do differently. Pointing at the spec you are reacting to helps.
- **Questions.** If the README, a page under [docs/](docs/index.md) or a spec left you guessing, that is a documentation bug worth an issue too.

## Working with the code anyway

The code is MIT-licensed, so forking it and building on it is welcome. The commands to lint, type check and run the two test tiers, and the checklist for adding a module, are in [AGENTS.md](AGENTS.md); the user documentation starts at [docs/index.md](docs/index.md). If your fork grows something you think belongs upstream, open an issue describing it.
