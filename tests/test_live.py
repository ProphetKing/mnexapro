import asyncio
import os
from dotenv import load_dotenv
from mnexa.llm.deepseek_client import DeepSeekClient

# 自动加载 .env 文件（优先从当前目录查找，找不到则向上查找）
load_dotenv()

async def main():
    client = DeepSeekClient()
    system_msg = "你是一个有帮助的助手。"
    user_msg = "你好，请用一句话介绍你自己。"

    print(f"System: {system_msg}")
    print(f"User: {user_msg}")
    print("--- 正在调用本地 DeepSeek 服务 ---")

    result = await client.complete(system=system_msg, user=user_msg)

    print(f"Assistant: {result.text}")
    print(f"Token 用量: input={result.usage.input_tokens}, output={result.usage.output_tokens}")

if __name__ == "__main__":
    asyncio.run(main())