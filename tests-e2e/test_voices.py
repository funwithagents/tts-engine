"""Live voice listing, parametrized over ``MODULES`` in ``support``.

Each backend lists its voices from its real catalog, given the same config the
conformance tests synthesize with (its voice fields are ignored by listing). A
backend with a catalog must return voices, and the ``config`` of one of them,
merged into the row's config, must build a module that speaks: the listing
says how to select a voice, and this checks it is right. The backends with no
catalog to read raise ``VoiceListingUnsupported``; the fixtures list none.

Rows skip like the other live tests (``support.require_module``). An ElevenLabs
key without the ``voices_read`` permission also skips: that is the key's
scope, not the module's behaviour.
"""

from __future__ import annotations

import logging

import pytest
from support import MODULES, CaptureSink, engine_block, require_module, spoken_text

from tts_engine import TTSEngine, VoiceInfo, VoiceListingUnsupported, list_voices
from tts_engine.config import TTSEngineConfig
from tts_engine.modules.base import TTSError

log = logging.getLogger(__name__)

_UNSUPPORTED = {"gemini", "openai", "openai-compatible"}
_FIXTURES = {"tone", "audiofile"}


def _pick(voices: list[VoiceInfo]) -> VoiceInfo:
    """An English voice when the catalog says which ones are, else the first."""
    for voice in voices:
        if any(language.lower().startswith("en") for language in voice.languages):
            return voice
    return voices[0]


@pytest.mark.parametrize("module_type, module_config", MODULES)
async def test_module_lists_voices_that_select_a_working_voice(
    module_type: str, module_config: dict
) -> None:
    require_module(module_type, module_config)
    listing_config = {"type": module_type, **module_config}

    if module_type in _UNSUPPORTED:
        with pytest.raises(VoiceListingUnsupported, match=r"https?://"):
            list_voices(listing_config)
        return

    try:
        voices = list_voices(listing_config)
    except TTSError as exc:
        if module_type == "elevenlabs" and "voices_read" in str(exc):
            pytest.skip(f"the ElevenLabs key cannot list voices: {exc}")
        raise

    if module_type in _FIXTURES:
        assert voices == []
        return

    assert voices, f"{module_type} listed no voices"
    assert len({voice.id for voice in voices}) == len(voices), "voice ids repeat"
    voice = _pick(voices)
    log.info(
        "%s: %d voices, speaking with %s %s",
        module_type,
        len(voices),
        voice.name,
        voice.config,
    )

    sink = CaptureSink()
    config = {**module_config, **voice.config}
    async with TTSEngine(
        TTSEngineConfig.from_dict(engine_block(module_type, config)), sink=sink
    ) as engine:
        await engine.say(spoken_text(module_type))
    assert len(sink.data) > 0 and len(sink.data) % 2 == 0
