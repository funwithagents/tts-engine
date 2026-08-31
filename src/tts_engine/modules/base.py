"""TTSModule ABC, TTSOptions, TTSError."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


class TTSError(Exception):
    pass


@dataclass
class TTSOptions:
    pass


class TTSModule(ABC):
    def __init__(self, config: dict) -> None:
        """Construct the module from its `engine.module` block (the full dict,
        including `type`). Subclasses validate and extract the fields they
        need, raising `ConfigError` for missing/invalid ones.
        """

    @abstractmethod
    async def stream(
        self,
        text: str,
        options: TTSOptions,
        callback: Callable[[bytes], None],
    ) -> None:
        """
        Synthesize `text` and call `callback` with each PCM chunk as it arrives.

        - `text`: the string to synthesize. Must not be empty.
        - `options`: per-call overrides; currently empty and reserved for future use.
        - `callback`: called sequentially with each raw audio chunk. Calls may run
          off the event-loop thread but must never overlap.

        Raises `TTSError` on synthesis failure.
        Returns or raises only after it has stopped invoking `callback`, including
        when the coroutine is cancelled.
        """
