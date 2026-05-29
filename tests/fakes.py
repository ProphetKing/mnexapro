from __future__ import annotations

from collections.abc import AsyncIterator

from mnexa.llm import Completion, Usage


class FakeLLMClient:
    """Canned-response LLM client for ingest integration tests."""

    model: str = "fake"

    def __init__(self, analysis: str, generation: str) -> None:
        self._analysis = analysis
        self._generation = generation
        self.last_usage: Usage | None = None

    async def complete(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> Completion:
        del system, user, cache_system
        usage = Usage(input_tokens=10, output_tokens=20, cached_input_tokens=0)
        self.last_usage = usage
        return Completion(text=self._analysis, usage=usage)

    async def stream(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> AsyncIterator[str]:
        del system, user, cache_system
        for i in range(0, len(self._generation), 64):
            yield self._generation[i : i + 64]
        self.last_usage = Usage(input_tokens=20, output_tokens=40, cached_input_tokens=0)
