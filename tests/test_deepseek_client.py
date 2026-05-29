import os
import pytest
from unittest.mock import AsyncMock, patch
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from mnexa.llm.deepseek_client import DeepSeekClient
from mnexa.llm.base import Completion, Usage

def make_mock_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 20):
    return ChatCompletion(
        id="test-id",
        object="chat.completion",
        created=1234567890,
        model="deepseek-r1",
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )

class TestDeepSeekClient:
    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("DEEPSEEK_API_BASE", "http://localhost:8000/v1")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-r1")

    @pytest.mark.asyncio
    async def test_complete_returns_correct_text(self, mocker):
        mock_response = make_mock_response("Hello, world!")

        client = DeepSeekClient()
        # 直接 mock 实例的 _client.chat.completions.create
        mock_create = AsyncMock(return_value=mock_response)
        mocker.patch.object(client._client.chat.completions, "create", mock_create)

        result = await client.complete(system="Be helpful", user="Hi")

        assert isinstance(result, Completion)
        assert result.text == "Hello, world!"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 20
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_handles_empty_content(self, mocker):
        mock_response = make_mock_response("")
        client = DeepSeekClient()
        mock_create = AsyncMock(return_value=mock_response)
        mocker.patch.object(client._client.chat.completions, "create", mock_create)

        result = await client.complete(system="", user="Question")
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_stream_yields_content(self, mocker):
        # 模拟流式异步迭代器
        async def mock_stream(*args, **kwargs):
            class FakeDelta:
                def __init__(self, content):
                    self.content = content
            class FakeChoice:
                def __init__(self, content):
                    self.delta = FakeDelta(content)
                    self.finish_reason = None
            class FakeChunk:
                def __init__(self, content):
                    self.choices = [FakeChoice(content)]
                    self.usage = None

            for text in ["Hello", ", ", "world!"]:
                yield FakeChunk(text)

        client = DeepSeekClient()
        mock_create = AsyncMock(return_value=mock_stream())
        mocker.patch.object(client._client.chat.completions, "create", mock_create)

        chunks = []
        async for chunk in client.stream(system="", user="Test"):
            chunks.append(chunk)

        assert "".join(chunks) == "Hello, world!"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is not set"):
            DeepSeekClient()