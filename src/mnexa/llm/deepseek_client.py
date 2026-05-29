"""DeepSeek LLM client for Mnexa (local OpenAI-compatible endpoint)."""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any, cast

from openai import AsyncOpenAI, APIStatusError
from openai.types.chat import ChatCompletionMessageParam

from mnexa.llm.base import Completion, Usage


class DeepSeekClient:
    """Async client that talks to a local DeepSeek service."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "DeepSeek-R1-local")
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. "
                "Please set it to your local service's bearer token."
            )
        base_url = os.environ.get(
            "DEEPSEEK_API_BASE",
            "http://172.18.2.223:38000/publishaddress/inference/413dc091/v1"
        )
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.last_usage: Usage | None = None

    async def _retry_on_rate_limit(self, coro):
        """重试逻辑：遇到 429 等待 5 秒，最多重试 3 次"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await coro
            except APIStatusError as e:
                if e.status_code == 429 and attempt < max_retries - 1:
                    await asyncio.sleep(15)
                    continue
                raise

    async def complete(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> Completion:
        del cache_system  # 本地模型不支持 prompt caching
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append(
                cast(ChatCompletionMessageParam, {"role": "system", "content": system})
            )
        messages.append(
            cast(ChatCompletionMessageParam, {"role": "user", "content": user})
        )

        response = await self._retry_on_rate_limit(
            self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )
        )
        usage = self._extract_usage(response.usage)
        self.last_usage = usage
        return Completion(
            text=response.choices[0].message.content or "",
            usage=usage
        )

    async def stream(
        self, *, system: str, user: str, cache_system: bool = False
    ) -> AsyncIterator[str]:
        del cache_system
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append(
                cast(ChatCompletionMessageParam, {"role": "system", "content": system})
            )
        messages.append(
            cast(ChatCompletionMessageParam, {"role": "user", "content": user})
        )

        stream = await self._retry_on_rate_limit(
            self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=4096,      # 与 complete 保持一致，防止超长
                stream=True,
            )
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage:
                self.last_usage = self._extract_usage(chunk.usage)

    @staticmethod
    def _extract_usage(usage: Any) -> Usage:
        if usage is None:
            return Usage(0, 0, 0)
        return Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cached_input_tokens=0,
        )