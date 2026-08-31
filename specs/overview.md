---
# Project-wide overview — governs no single module, so `code:` is intentionally omitted.
tests: []
---

# Overview

**Status:** Stable

## Purpose

`tts-mcp` is an MCP server that exposes text-to-speech synthesis as a tool. An MCP client (e.g. an AI agent or Claude Desktop) calls the `speak` tool with a text string; the server synthesizes the audio through a cloud TTS provider and plays it in real-time on the server machine's audio output.

## Goals

- Provide a single, clean `speak` tool usable from any MCP client
- Stream audio from the provider to the audio device with minimal latency (playback starts before the full audio is received)
- Support pluggable TTS backends, with ElevenLabs as the first implementation
- Be deployable on a local network (StreamableHTTP transport)
- Keep configuration simple: one config file, one active module

## Non-goals (v1)

- No file output / audio asset generation (`synthesize` tool)
- No voice listing (`list_voices` tool)
- No MCP resources
- No audio content verification
- No multi-module routing or fallback
- No client-side audio streaming (bytes returned to MCP client)

## Intended use

The server runs on a machine with speakers. An AI agent or human using an MCP client sends text to the `speak` tool and hears the result immediately on that machine.

Example use cases:
- AI assistant reads out a response aloud
- Notification system speaks alerts
- Developer testing TTS output during model/voice tuning
