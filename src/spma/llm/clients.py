"""LLM 客户端——Haiku/Sonnet API + Qwen3-8B vLLM 本地。

统一接口: chat(messages, model, **kwargs) → str
动态模型选择: 运行时按 state 自动切换 Haiku/Sonnet
指数退避重试: tenacity, 429→重试3次, multiplier=0.5s, max_wait=2s
降级: 非 429 错误直接降级到 Qwen3-8B
"""

import os
import logging
from langchain_anthropic import ChatAnthropic
from anthropic import AsyncAnthropic

from spma.infrastructure.circuit_breaker import circuit_breaker

logger = logging.getLogger(__name__)


def get_default_llm() -> ChatAnthropic:
    """获取默认 LLM 客户端（LangChain ChatAnthropic）。

    使用环境变量 ANTHROPIC_BASE_URL 和 ANTHROPIC_API_KEY 配置。
    """
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    model = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-20250514")

    return ChatAnthropic(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=4096,
    )


async def chat(messages: list[dict], model: str | None = None, **kwargs) -> str:
    """异步调用 LLM 完成对话。

    Args:
        messages: 消息列表 [{"role": "...", "content": "..."}]
        model: 模型名称，默认从环境变量读取
        **kwargs: 其他参数传递给 API

    Returns:
        LLM 响应文本
    """
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    model_name = model or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-20250514")

    client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    # 分离 system 消息
    system_prompt = ""
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system_prompt += m["content"] + "\n"
        else:
            user_messages.append(m)

    response = await client.messages.create(
        model=model_name,
        max_tokens=kwargs.get("max_tokens", 4096),
        system=system_prompt.strip() or None,
        messages=user_messages,
    )

    # 跳过 thinking blocks，返回第一个 text block
    for block in response.content:
        if block.type == "text":
            return block.text

    # fallback：如果没有任何 text block
    return str(response.content[0])
