# src/spma/llm/providers/openai_compat.py
"""OpenAI 兼容 Provider——覆盖 DeepSeek、OpenAI 及任何 OpenAI 兼容 API。

通过构造时的 ProviderConfig.base_url 区分不同提供商。
thinking 参数通过 extra_body 传递给 DeepSeek API。
"""

import logging

import httpx
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from spma.llm.providers.base import (
    LLMClientError,
    LLMProvider,
    LLMRateLimitError,
    LLMServiceError,
    ProviderConfig,
)

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容协议的 Provider——DeepSeek/OpenAI/任何 OpenAI 兼容 API。"""

    def __init__(self, name: str, config: ProviderConfig):
        super().__init__(name, config)
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        # 判断是否为 vLLM（本地模型不支持 thinking）
        self._vllm = "vllm" in config.base_url.lower()

    async def chat(self, messages: list[dict], model: str, **kwargs) -> str:
        formatted = messages

        api_kwargs: dict = {
            "model": model,
            "messages": formatted,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.3),
        }

        # DeepSeek V4 thinking mode：通过 extra_body 传递
        if "thinking" in kwargs and not self._vllm:
            api_kwargs["extra_body"] = {"thinking": kwargs["thinking"]}

        try:
            response = await self._client.chat.completions.create(**api_kwargs)
            return response.choices[0].message.content or ""
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                retry_after = float(e.response.headers.get("Retry-After", 1.0))
                raise LLMRateLimitError(f"Rate limited by {self.name}", retry_after=retry_after) from e
            elif status >= 500:
                raise LLMServiceError(f"{self.name} server error", status_code=status) from e
            elif status >= 400:
                raise LLMClientError(f"{self.name} client error", status_code=status) from e
            raise

    async def ping(self) -> bool:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.default_model or "deepseek-v4-flash",
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            return response.choices[0].message.content is not None
        except Exception as e:
            logger.warning(f"{self.name} ping 失败: {e}")
            return False

    def supports_thinking(self) -> bool:
        return not self._vllm

    def get_langchain_client(self, model: str):
        return ChatOpenAI(
            model=model,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=0.3,
            max_tokens=4096,
        )
