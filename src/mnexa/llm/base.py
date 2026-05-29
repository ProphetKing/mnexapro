"""Provider-agnostic LLM client protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int  # 0 for providers without prompt caching

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


class LLMClient(Protocol):
    model: str
    last_usage: Usage | None

    async def complete(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> Completion: ...

    def stream(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> AsyncIterator[str]: ...
