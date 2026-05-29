"""Google Gemini LLM client.

Uses google-genai SDK. Lazy-imported so non-Gemini callers don't pay
the import cost.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from mnexa.llm.base import Completion, Usage


class GeminiClient:
    def __init__(self, model: str) -> None:
        from google import genai
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY) is not set. "
                "Get a key at https://aistudio.google.com/apikey."
            )
        self.model = model
        self._client = genai.Client(api_key=api_key)
        self.last_usage: Usage | None = None

    async def complete(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> Completion:
        from google.genai import types
        del cache_system  # Gemini context-cache thresholds make this a no-op for v0.
        config = types.GenerateContentConfig(system_instruction=system)
        resp = await self._client.aio.models.generate_content(
            model=self.model, contents=user, config=config
        )
        usage = _extract_usage(resp.usage_metadata)
        self.last_usage = usage
        return Completion(text=resp.text or "", usage=usage)

    async def stream(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> AsyncIterator[str]:
        from google.genai import types
        del cache_system
        config = types.GenerateContentConfig(system_instruction=system)
        stream = await self._client.aio.models.generate_content_stream(
            model=self.model, contents=user, config=config
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
            if chunk.usage_metadata:
                self.last_usage = _extract_usage(chunk.usage_metadata)


def _extract_usage(meta: Any) -> Usage:
    if meta is None:
        return Usage(0, 0, 0)
    return Usage(
        input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        cached_input_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
    )
