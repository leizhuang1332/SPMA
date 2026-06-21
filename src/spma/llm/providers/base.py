# src/spma/llm/providers/base.py
"""LLM Provider 抽象基类、数据模型和异常类。

定义所有 Provider 必须实现的统一接口，以及配置加载和角色路由所需的数据结构。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal

# ── 异常类 ──────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """LLM 相关异常的基类。"""
    pass


class LLMConfigError(LLMError):
    """配置错误：provider 类型未知、缺少必需字段等。"""
    pass


class LLMRateLimitError(LLMError):
    """429 限流错误——可重试。"""

    def __init__(self, message: str, retry_after: float = 1.0):
        super().__init__(message)
        self.retry_after = retry_after


class LLMServiceError(LLMError):
    """5xx 服务端错误——可重试。"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class LLMClientError(LLMError):
    """4xx 客户端错误——不可重试。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class LLMUnavailableError(LLMError):
    """所有 provider（含 fallback）都不可用。"""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


# ── 配置数据模型 ────────────────────────────────────────────────────────────

@dataclass
class ProviderConfig:
    """单个 LLM 提供商的配置。"""
    type: str                            # "anthropic" | "openai_compat"
    api_key: str
    base_url: str
    default_model: str | None = None


@dataclass(init=False)
class RoleConfig:
    """角色槽位配置——将 role 绑定到 (provider, model)。"""
    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: str | None = None         # "enabled" | None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)

    def __init__(self, provider: str, model: str, max_tokens: int = 4096,
                 temperature: float = 0.3, thinking: str | None = None, **kwargs):
        """支持任意额外的 provider 特定参数，自动归入 extra_kwargs。"""
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking
        self.extra_kwargs = kwargs

    @classmethod
    def from_dict(cls, data: dict) -> "RoleConfig":
        known = {"provider", "model", "max_tokens", "temperature", "thinking"}
        kwargs = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(**kwargs, **extra)


@dataclass
class RetryConfig:
    """重试配置。"""
    max_retries: int = 3
    multiplier_seconds: float = 0.5
    max_wait_seconds: float = 2.0


# ── StreamChunk ──────────────────────────────────────────────────────────────

@dataclass
class StreamChunk:
    """LLM 流式响应的单个 chunk——区分思考 token 和输出 token。"""
    type: Literal["thinking", "output"]
    content: str
    model: str | None = None
    finish_reason: str | None = None  # "stop" | "length" | None


# ── 抽象基类 ────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """所有 LLM 提供商的统一抽象接口。

    每个 Provider 封装一种 API 格式（Anthropic 原生 / OpenAI 兼容），
    对外暴露统一的 chat()、ping()、get_langchain_client() 方法。
    """

    def __init__(self, name: str, config: ProviderConfig):
        self.name = name
        self._config = config

    @abstractmethod
    async def chat(self, messages: list[dict], model: str, **kwargs) -> str:
        """异步对话，返回文本响应。

        Args:
            messages: [{"role": "...", "content": "..."}]
            model: 模型名称
            **kwargs: 传递给 API 的额外参数（max_tokens, temperature, thinking 等）

        Returns:
            LLM 响应的文本内容
        """
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """健康检查——用于降级系统的 provider 可用性判断。"""
        ...

    def supports_thinking(self) -> bool:
        """是否支持思考链模式。Anthropic 和 DeepSeek 返回 True，其他返回 False。"""
        return False

    def get_langchain_client(self, model: str):
        """返回 LangChain 兼容的 ChatModel，供 LangGraph StateGraph 使用。

        各子类覆写此方法返回对应的 LangChain 客户端类型。
        """
        raise NotImplementedError(f"{self.name} 不支持 get_langchain_client")

    async def astream(self, messages: list[dict], model: str, **kwargs) -> AsyncGenerator[StreamChunk, None]:
        """流式对话——yield StreamChunk 逐个返回思考和输出 token。

        默认实现回退到同步 chat()，将整个响应作为一个 output chunk 返回。
        支持 streaming 的 Provider 应覆写此方法。
        """
        text = await self.chat(messages, model, **kwargs)
        yield StreamChunk(type="output", content=text, model=model, finish_reason="stop")

    @property
    def default_model(self) -> str | None:
        return self._config.default_model
