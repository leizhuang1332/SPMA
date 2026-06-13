# src/spma/llm/providers/anthropic.py
"""Anthropic Provider——封装 AsyncAnthropic + ChatAnthropic。

支持 Claude extended thinking 模式，自动跳过 thinking blocks 返回 text content。
"""

import logging

from anthropic import AsyncAnthropic
from langchain_anthropic import ChatAnthropic

from spma.llm.providers.base import LLMProvider, ProviderConfig

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic (Claude) 系列模型的 Provider。"""

    def __init__(self, name: str, config: ProviderConfig):
        super().__init__(name, config)
        self._client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def chat(self, messages: list[dict], model: str, **kwargs) -> str:
        system_prompt = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt += m["content"] + "\n"
            else:
                user_messages.append(m)

        api_kwargs: dict = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "system": system_prompt.strip() or None,
            "messages": user_messages,
        }

        # Claude extended thinking
        if "thinking" in kwargs:
            api_kwargs["thinking"] = kwargs["thinking"]

        response = await self._client.messages.create(**api_kwargs)

        for block in response.content:
            if block.type == "text":
                return block.text

        return str(response.content[0])

    async def ping(self) -> bool:
        try:
            response = await self._client.messages.create(
                model=self._config.default_model or "claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return any(block.type == "text" for block in response.content)
        except Exception as e:
            logger.warning(f"Anthropic ping 失败: {e}")
            return False

    def supports_thinking(self) -> bool:
        return True

    def get_langchain_client(self, model: str):
        return ChatAnthropic(
            model=model,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=0.3,
            max_tokens=4096,
        )
