# 多提供商 LLM 抽象层 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SPMA 的 LLM 调用从硬编码 Anthropic 改造为多提供商、可配置、可运行时热切换的抽象层。

**Architecture:** Provider 抽象（封装 API 差异）→ Role 槽位（命名映射 provider+model）→ LLMRouter 单例（线程安全路由+热切换）。外部通过 Admin API 或 Feature Flag 下发切换指令，与现有降级系统联动。

**Tech Stack:** Python 3.13+, `openai` SDK, `langchain-anthropic`, `langchain-openai`, `tenacity`, `threading.RLock`, `pydantic`, `pyyaml`

**Design Doc:** [2026-06-13-multi-provider-llm-design.md](../specs/2026-06-13-multi-provider-llm-design.md)

---

## File Structure

```
新增:
  src/spma/llm/providers/__init__.py      # 注册表 by_name()
  src/spma/llm/providers/base.py          # LLMProvider ABC + RoleConfig + LLMError 类
  src/spma/llm/providers/anthropic.py     # AnthropicProvider
  src/spma/llm/providers/openai_compat.py # OpenAICompatProvider
  src/spma/llm/providers/local_vllm.py    # LocalVLLMProvider
  src/spma/llm/router.py                  # LLMRouter 单例 + LLMConfig 加载
  src/spma/api/routes/llm_admin.py        # /admin/llm/* 管理端点
  tests/unit/llm/test_providers.py        # Provider 单元测试
  tests/unit/llm/test_router.py           # Router 单元测试
  tests/integration/test_llm_router.py    # Router 集成测试

改造:
  src/spma/llm/__init__.py                # 从 router 导出 chat/get_langchain_client
  src/spma/llm/clients.py                 # 标记废弃，委托给 router
  src/spma/config/constants.py            # 移除硬编码 MODEL_* 常量
  config/spma.yaml                        # llm 段改为新结构
  config/feature_flags.yaml               # 增加 llm_role_overrides
  src/spma/api/app.py                     # 注册 llm_admin 路由
  src/spma/api/routes/query.py            # get_default_llm() → router
  src/spma/agents/sql/generator.py        # 硬编码 model → role
  src/spma/agents/sql/verifier.py         # 硬编码 model → role
  src/spma/infrastructure/degradation/actions/l1_llm.py  # 动态 role 切换
  tests/conftest.py                       # mock_llm fixture 改为 MultiProviderMock
```

---

### Task 1: 定义异常类和 RoleConfig 数据模型

**Files:**
- Create: `src/spma/llm/providers/__init__.py`
- Create: `src/spma/llm/providers/base.py`
- Create: `tests/unit/llm/__init__.py` (如果不存在)

- [ ] **Step 1: 创建 providers 包的 `__init__.py`**

```python
# src/spma/llm/providers/__init__.py
"""LLM Provider 注册表。

通过 name 查找已注册的 provider 实例。
"""

from spma.llm.providers.base import LLMProvider, RoleConfig

_registry: dict[str, "LLMProvider"] = {}


def register(name: str, provider: "LLMProvider") -> None:
    """注册一个 provider 实例。"""
    _registry[name] = provider


def by_name(name: str) -> "LLMProvider":
    """按名称获取 provider，未注册则抛出 KeyError。"""
    if name not in _registry:
        available = list(_registry.keys())
        raise KeyError(f"Provider '{name}' 未注册。可用: {available}")
    return _registry[name]


def list_all() -> dict[str, "LLMProvider"]:
    """返回所有已注册 provider 的只读视图。"""
    return dict(_registry)
```

- [ ] **Step 2: 运行测试验证模块可导入**

```bash
uv run python -c "from spma.llm.providers import register, by_name, list_all; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 3: 编写异常类和 RoleConfig 的测试**

```python
# tests/unit/llm/test_providers.py
"""Provider 基类和异常类单元测试。"""

import pytest
from spma.llm.providers.base import (
    LLMProvider,
    RoleConfig,
    ProviderConfig,
    LLMRateLimitError,
    LLMServiceError,
    LLMClientError,
    LLMUnavailableError,
    LLMConfigError,
)


class TestLLMErrors:
    def test_rate_limit_error_is_retryable(self):
        err = LLMRateLimitError("rate limited", retry_after=2.0)
        assert err.retry_after == 2.0
        assert "rate limited" in str(err)

    def test_service_error_is_retryable(self):
        err = LLMServiceError("server error", status_code=503)
        assert err.status_code == 503
        assert "server error" in str(err)

    def test_client_error_not_retryable(self):
        err = LLMClientError("bad request", status_code=400)
        assert err.status_code == 400

    def test_unavailable_error_has_cause(self):
        cause = ValueError("connection refused")
        err = LLMUnavailableError("all providers failed", cause=cause)
        assert err.cause is cause
        assert "all providers failed" in str(err)

    def test_config_error_for_invalid_config(self):
        err = LLMConfigError("unknown provider type: xyz")
        assert "unknown provider type" in str(err)


class TestRoleConfig:
    def test_default_values(self):
        cfg = RoleConfig(provider="test", model="test-model")
        assert cfg.provider == "test"
        assert cfg.model == "test-model"
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.3
        assert cfg.thinking is None

    def test_with_thinking(self):
        cfg = RoleConfig(provider="test", model="test-model", thinking="enabled")
        assert cfg.thinking == "enabled"

    def test_with_custom_kwargs(self):
        cfg = RoleConfig(provider="test", model="test-model", extra_param="value")
        assert cfg.extra_kwargs == {"extra_param": "value"}


class TestProviderConfig:
    def test_minimal_config(self):
        cfg = ProviderConfig(type="openai_compat", api_key="sk-xxx", base_url="https://api.example.com")
        assert cfg.type == "openai_compat"
        assert cfg.api_key == "sk-xxx"
        assert cfg.default_model is None

    def test_with_default_model(self):
        cfg = ProviderConfig(
            type="anthropic",
            api_key="sk-xxx",
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-6",
        )
        assert cfg.default_model == "claude-sonnet-4-6"
```

- [ ] **Step 4: 运行测试，验证失败**

```bash
uv run pytest tests/unit/llm/test_providers.py -v
```
Expected: 全部 FAIL（类尚未定义）

- [ ] **Step 5: 编写 `base.py`——异常类和配置数据模型**

```python
# src/spma/llm/providers/base.py
"""LLM Provider 抽象基类、数据模型和异常类。

定义所有 Provider 必须实现的统一接口，以及配置加载和角色路由所需的数据结构。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class RoleConfig:
    """角色槽位配置——将 role 绑定到 (provider, model)。"""
    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: str | None = None         # "enabled" | None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "RoleConfig":
        known = {"provider", "model", "max_tokens", "temperature", "thinking"}
        kwargs = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known and k != "provider" and k != "model"}
        return cls(**kwargs, extra_kwargs=extra)


@dataclass
class RetryConfig:
    """重试配置。"""
    max_retries: int = 3
    multiplier_seconds: float = 0.5
    max_wait_seconds: float = 2.0


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

    @property
    def default_model(self) -> str | None:
        return self._config.default_model
```

- [ ] **Step 6: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_providers.py -v
```
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add src/spma/llm/providers/ tests/unit/llm/
git commit -m "feat: add LLM provider base classes, error types, and config models"
```

---

### Task 2: 实现 AnthropicProvider

**Files:**
- Create: `src/spma/llm/providers/anthropic.py`
- Modify: `tests/unit/llm/test_providers.py` (追加测试)

- [ ] **Step 1: 追加 AnthropicProvider 测试**

在 `tests/unit/llm/test_providers.py` 末尾追加：

```python
from unittest.mock import AsyncMock, patch, MagicMock


class TestAnthropicProvider:
    @pytest.fixture
    def provider(self):
        from spma.llm.providers.anthropic import AnthropicProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="anthropic",
            api_key="sk-test",
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-6",
        )
        return AnthropicProvider("test_anthropic", cfg)

    def test_name(self, provider):
        assert provider.name == "test_anthropic"

    def test_supports_thinking(self, provider):
        assert provider.supports_thinking() is True

    @pytest.mark.asyncio
    async def test_chat_returns_text(self, provider):
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "Hello from Claude"
        mock_response.content = [mock_content]

        with patch.object(provider, '_client') as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await provider.chat(
                [{"role": "user", "content": "Hi"}],
                model="claude-sonnet-4-6",
            )
            assert result == "Hello from Claude"

    @pytest.mark.asyncio
    async def test_chat_strips_thinking_blocks(self, provider):
        mock_response = MagicMock()
        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Final answer"
        mock_response.content = [thinking_block, text_block]

        with patch.object(provider, '_client') as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await provider.chat(
                [{"role": "user", "content": "Hi"}],
                model="claude-sonnet-4-6",
            )
            assert result == "Final answer"

    @pytest.mark.asyncio
    async def test_chat_passes_thinking_param(self, provider):
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "Deep thinking result"
        mock_response.content = [mock_content]

        with patch.object(provider, '_client') as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            await provider.chat(
                [{"role": "user", "content": "Complex question"}],
                model="claude-sonnet-4-6",
                thinking={"type": "enabled", "budget_tokens": 2048},
                max_tokens=4096,
            )
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 2048}
            assert call_kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_ping_returns_true(self, provider):
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "pong"
        mock_response.content = [mock_content]

        with patch.object(provider, '_client') as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await provider.ping()
            assert result is True

    @pytest.mark.asyncio
    async def test_ping_returns_false_on_error(self, provider):
        with patch.object(provider, '_client') as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=Exception("connection refused"))
            result = await provider.ping()
            assert result is False

    def test_get_langchain_client(self, provider):
        client = provider.get_langchain_client("claude-sonnet-4-6")
        from langchain_anthropic import ChatAnthropic
        assert isinstance(client, ChatAnthropic)
        assert client.model == "claude-sonnet-4-6"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestAnthropicProvider -v
```
Expected: 全部 FAIL（`AnthropicProvider` 未定义）

- [ ] **Step 3: 实现 AnthropicProvider**

```python
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
            for block in response.content:
                if block.type == "text":
                    return True
            return False
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
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestAnthropicProvider -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/llm/providers/anthropic.py tests/unit/llm/test_providers.py
git commit -m "feat: implement AnthropicProvider with Claude thinking support"
```

---

### Task 3: 实现 OpenAICompatProvider

**Files:**
- Create: `src/spma/llm/providers/openai_compat.py`
- Modify: `tests/unit/llm/test_providers.py` (追加测试)

- [ ] **Step 1: 追加 OpenAICompatProvider 测试**

在 `tests/unit/llm/test_providers.py` 末尾追加：

```python
class TestOpenAICompatProvider:
    @pytest.fixture
    def provider(self):
        from spma.llm.providers.openai_compat import OpenAICompatProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="openai_compat",
            api_key="sk-deepseek-test",
            base_url="https://api.deepseek.com",
            default_model="deepseek-v4-pro",
        )
        return OpenAICompatProvider("test_deepseek", cfg)

    def test_name(self, provider):
        assert provider.name == "test_deepseek"

    def test_supports_thinking_is_true_for_deepseek(self, provider):
        # DeepSeek V4 supports thinking mode
        assert provider.supports_thinking() is True

    def test_supports_thinking_is_false_for_vllm(self):
        from spma.llm.providers.openai_compat import OpenAICompatProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="openai_compat",
            api_key="not-needed",
            base_url="http://vllm.internal:8000/v1",
            default_model="qwen3-8b-local",
        )
        # base_url 包含 vllm → disable thinking
        provider = OpenAICompatProvider("test_vllm", cfg)
        assert provider.supports_thinking() is False

    @pytest.mark.asyncio
    async def test_chat_returns_text(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from DeepSeek"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            result = await provider.chat(
                [{"role": "user", "content": "Hi"}],
                model="deepseek-v4-pro",
            )
            assert result == "Hello from DeepSeek"

    @pytest.mark.asyncio
    async def test_chat_passes_thinking_for_deepseek(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Thinking result"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            await provider.chat(
                [{"role": "user", "content": "Complex"}],
                model="deepseek-v4-pro",
                thinking={"type": "enabled"},
            )
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

    @pytest.mark.asyncio
    async def test_chat_ignores_thinking_for_vllm(self):
        """vLLM 不支持 thinking，传入的 thinking 参数应被忽略。"""
        from spma.llm.providers.openai_compat import OpenAICompatProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="openai_compat",
            api_key="not-needed",
            base_url="http://vllm.internal:8000/v1",
        )
        provider = OpenAICompatProvider("test_vllm", cfg)

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Normal result"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            await provider.chat(
                [{"role": "user", "content": "Hi"}],
                model="qwen3-8b",
                thinking={"type": "enabled"},
            )
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert "extra_body" not in call_kwargs

    @pytest.mark.asyncio
    async def test_ping_returns_true(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "pong"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            result = await provider.ping()
            assert result is True

    @pytest.mark.asyncio
    async def test_ping_returns_false_on_error(self, provider):
        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                side_effect=Exception("connection refused")
            )
            result = await provider.ping()
            assert result is False

    @pytest.mark.asyncio
    async def test_rate_limit_error_raised(self, provider):
        import httpx

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429 Too Many Requests",
                    request=MagicMock(),
                    response=MagicMock(status_code=429, headers={"Retry-After": "2"}),
                )
            )
            with pytest.raises(Exception):
                await provider.chat([{"role": "user", "content": "Hi"}], model="test")

    @pytest.mark.asyncio
    async def test_chat_extracts_system_message(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Answer"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            await provider.chat(
                [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hi"},
                ],
                model="deepseek-v4-pro",
            )
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            messages = call_kwargs["messages"]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == "You are helpful."
            assert messages[1]["role"] == "user"
            assert messages[1]["content"] == "Hi"

    def test_get_langchain_client(self, provider):
        client = provider.get_langchain_client("deepseek-v4-pro")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)
        assert client.model_name == "deepseek-v4-pro"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestOpenAICompatProvider -v
```
Expected: 全部 FAIL（`OpenAICompatProvider` 未定义）

- [ ] **Step 3: 实现 OpenAICompatProvider**

```python
# src/spma/llm/providers/openai_compat.py
"""OpenAI 兼容 Provider——覆盖 DeepSeek、OpenAI 及任何 OpenAI 兼容 API。

通过构造时的 ProviderConfig.base_url 区分不同提供商。
thinking 参数通过 extra_body 传递给 DeepSeek API。
"""

import logging
import httpx
from openai import AsyncOpenAI
from langchain_openai import ChatOpenAI

from spma.llm.providers.base import (
    LLMProvider,
    ProviderConfig,
    LLMRateLimitError,
    LLMServiceError,
    LLMClientError,
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
        # 构建 OpenAI 格式的 messages（system role 直接传入）
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
        # 仅 DeepSeek（非 vLLM）支持 thinking
        return not self._vllm

    def get_langchain_client(self, model: str):
        return ChatOpenAI(
            model=model,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            temperature=0.3,
            max_tokens=4096,
        )
```

- [ ] **Step 4: 检查 `langchain-openai` 是否在依赖中**

```bash
uv run python -c "from langchain_openai import ChatOpenAI; print('OK')"
```
Expected: 输出 `OK`。如果失败，运行 `uv add langchain-openai`

- [ ] **Step 5: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestOpenAICompatProvider -v
```
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/spma/llm/providers/openai_compat.py tests/unit/llm/test_providers.py
git commit -m "feat: implement OpenAICompatProvider for DeepSeek/OpenAI/vLLM"
```

---

### Task 4: 实现 LocalVLLMProvider

**Files:**
- Create: `src/spma/llm/providers/local_vllm.py`
- Modify: `tests/unit/llm/test_providers.py` (追加测试)

- [ ] **Step 1: 追加 LocalVLLMProvider 测试**

在 `tests/unit/llm/test_providers.py` 末尾追加：

```python
class TestLocalVLLMProvider:
    @pytest.fixture
    def provider(self):
        from spma.llm.providers.local_vllm import LocalVLLMProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="openai_compat",
            api_key="not-needed",
            base_url="http://vllm.internal:8000/v1",
            default_model="qwen3-8b-local",
        )
        return LocalVLLMProvider("test_local", cfg)

    def test_name(self, provider):
        assert provider.name == "test_local"

    def test_supports_thinking_is_false(self, provider):
        assert provider.supports_thinking() is False

    def test_default_base_url(self):
        """不传 base_url 时使用默认 vLLM 地址。"""
        from spma.llm.providers.local_vllm import LocalVLLMProvider
        from spma.llm.providers.base import ProviderConfig

        cfg = ProviderConfig(
            type="openai_compat",
            api_key="not-needed",
            base_url="",
        )
        provider = LocalVLLMProvider("test_local", cfg)
        # 验证 base_url 被默认填充
        assert "vllm" in provider._config.base_url or provider._config.base_url == "http://localhost:8000/v1"

    @pytest.mark.asyncio
    async def test_chat_returns_text(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Local model response"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            result = await provider.chat(
                [{"role": "user", "content": "Hi"}],
                model="qwen3-8b-local",
            )
            assert result == "Local model response"

    @pytest.mark.asyncio
    async def test_ping_returns_true(self, provider):
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "pong"
        mock_response.choices = [mock_choice]

        with patch.object(provider, '_client') as mock_client:
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            result = await provider.ping()
            assert result is True

    def test_get_langchain_client(self, provider):
        client = provider.get_langchain_client("qwen3-8b-local")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)
        assert client.model_name == "qwen3-8b-local"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestLocalVLLMProvider -v
```
Expected: 全部 FAIL

- [ ] **Step 3: 实现 LocalVLLMProvider**

```python
# src/spma/llm/providers/local_vllm.py
"""本地 vLLM Provider——用于 L1 降级兜底。

继承 OpenAICompatProvider，覆盖默认 base_url 为本地 vLLM 地址，
禁用 thinking mode。
"""

from spma.llm.providers.openai_compat import OpenAICompatProvider
from spma.llm.providers.base import ProviderConfig


class LocalVLLMProvider(OpenAICompatProvider):
    """本地 vLLM 部署的 LLM Provider（如 Qwen3-8B）。

    与 OpenAICompatProvider 的唯一区别：
    - 默认 base_url 指向本地 vLLM 服务
    - supports_thinking() 始终返回 False
    """

    def __init__(self, name: str, config: ProviderConfig):
        # 确保 base_url 指向本地 vLLM
        if not config.base_url:
            config.base_url = "http://localhost:8000/v1"
        super().__init__(name, config)

    def supports_thinking(self) -> bool:
        return False
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_providers.py::TestLocalVLLMProvider -v
```
Expected: 全部 PASS

- [ ] **Step 5: 运行全部 Provider 测试确保无回归**

```bash
uv run pytest tests/unit/llm/test_providers.py -v
```
Expected: 全部 PASS（3 个测试类）

- [ ] **Step 6: 提交**

```bash
git add src/spma/llm/providers/local_vllm.py tests/unit/llm/test_providers.py
git commit -m "feat: implement LocalVLLMProvider as a thin OpenAICompatProvider wrapper"
```

---

### Task 5: 实现 LLMConfig 配置加载

**Files:**
- Create: `src/spma/llm/router.py`（前半部分：LLMConfig 类 + 加载函数）
- Create: `tests/unit/llm/test_router.py`

- [ ] **Step 1: 编写 LLMConfig 加载测试**

```python
# tests/unit/llm/test_router.py
"""LLMConfig 加载和 LLMRouter 单元测试。"""

import os
import tempfile
import pytest
from spma.llm.providers.base import (
    ProviderConfig,
    RoleConfig,
    RetryConfig,
    LLMConfigError,
)


class TestLLMConfigFromYAML:
    def make_yaml(self, content: str) -> str:
        """创建临时 YAML 文件并返回路径。"""
        import tempfile
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_load_minimal_config(self):
        yaml = """
llm:
  providers:
    test_ai:
      type: openai_compat
      api_key: sk-test
      base_url: https://test.api.com
  roles:
    default:
      provider: test_ai
      model: test-model
"""
        from spma.llm.router import load_llm_config
        config = load_llm_config(self.make_yaml(yaml))

        assert "test_ai" in config.providers
        assert config.providers["test_ai"].type == "openai_compat"
        assert config.roles["default"].provider == "test_ai"
        assert config.roles["default"].model == "test-model"

    def test_load_with_multiple_providers(self):
        yaml = """
llm:
  providers:
    anthropic:
      type: anthropic
      api_key: sk-ant
      base_url: https://api.anthropic.com
      default_model: claude-sonnet-4-6
    deepseek:
      type: openai_compat
      api_key: sk-ds
      base_url: https://api.deepseek.com
      default_model: deepseek-v4-pro
  roles:
    classification:
      provider: deepseek
      model: deepseek-v4-flash
      max_tokens: 2048
    generation:
      provider: deepseek
      model: deepseek-v4-pro
      thinking: enabled
    fallback:
      provider: local_vllm
      model: qwen3-8b-local
  retry:
    max_retries: 5
    multiplier_seconds: 1.0
    max_wait_seconds: 5.0
"""
        from spma.llm.router import load_llm_config
        config = load_llm_config(self.make_yaml(yaml))

        assert len(config.providers) == 2  # anthropic + deepseek (local_vllm not registered)
        assert config.providers["deepseek"].type == "openai_compat"

        assert config.roles["classification"].max_tokens == 2048
        assert config.roles["generation"].thinking == "enabled"
        assert config.roles["fallback"].provider == "local_vllm"

        assert config.retry.max_retries == 5
        assert config.retry.max_wait_seconds == 5.0

    def test_load_with_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SPMA_LLM_ROLE_GENERATION_PROVIDER", "anthropic")
        monkeypatch.setenv("SPMA_LLM_ROLE_GENERATION_MODEL", "claude-opus-4-8")

        yaml = """
llm:
  providers:
    anthropic:
      type: anthropic
      api_key: sk-ant
      base_url: https://api.anthropic.com
    deepseek:
      type: openai_compat
      api_key: sk-ds
      base_url: https://api.deepseek.com
  roles:
    generation:
      provider: deepseek
      model: deepseek-v4-pro
"""
        from spma.llm.router import load_llm_config
        config = load_llm_config(self.make_yaml(yaml))

        assert config.roles["generation"].provider == "anthropic"
        assert config.roles["generation"].model == "claude-opus-4-8"

    def test_load_with_env_var_provider_key(self, monkeypatch):
        monkeypatch.setenv("SPMA_LLM_PROVIDER_DEEPSEEK_API_KEY", "sk-env-override")

        yaml = """
llm:
  providers:
    deepseek:
      type: openai_compat
      api_key: sk-from-yaml
      base_url: https://api.deepseek.com
  roles:
    default:
      provider: deepseek
      model: deepseek-v4-pro
"""
        from spma.llm.router import load_llm_config
        config = load_llm_config(self.make_yaml(yaml))

        assert config.providers["deepseek"].api_key == "sk-env-override"

    def test_load_without_llm_section_raises(self):
        yaml = """
spma:
  version: "1.0"
"""
        from spma.llm.router import load_llm_config
        with pytest.raises(LLMConfigError, match="缺少 'llm' 配置段"):
            load_llm_config(self.make_yaml(yaml))

    def test_load_without_roles_raises(self):
        yaml = """
llm:
  providers:
    test:
      type: openai_compat
      api_key: sk
      base_url: https://test.com
"""
        from spma.llm.router import load_llm_config
        with pytest.raises(LLMConfigError, match="至少需要配置 default role"):
            load_llm_config(self.make_yaml(yaml))

    def test_load_with_unregistered_provider_in_role_raises(self):
        yaml = """
llm:
  providers:
    deepseek:
      type: openai_compat
      api_key: sk
      base_url: https://api.deepseek.com
  roles:
    default:
      provider: nonexistent
      model: some-model
"""
        from spma.llm.router import load_llm_config
        with pytest.raises(LLMConfigError, match="未在 providers 中注册"):
            load_llm_config(self.make_yaml(yaml))

    def test_default_role_is_generated_if_missing(self):
        yaml = """
llm:
  providers:
    deepseek:
      type: openai_compat
      api_key: sk
      base_url: https://api.deepseek.com
      default_model: deepseek-v4-pro
  roles:
    classification:
      provider: deepseek
      model: deepseek-v4-flash
"""
        from spma.llm.router import load_llm_config
        config = load_llm_config(self.make_yaml(yaml))

        # 如果未显式配置 default role，自动从第一个 provider 生成
        assert "default" in config.roles
        assert config.roles["default"].provider is not None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/llm/test_router.py::TestLLMConfigFromYAML -v
```
Expected: 全部 FAIL（`load_llm_config` 未定义）

- [ ] **Step 3: 实现 LLMConfig 和 load_llm_config**

```python
# src/spma/llm/router.py
"""LLM 路由层——LLMRouter 单例 + 配置加载 + 热切换。

核心职责:
1. 从 YAML + 环境变量加载 LLMConfig
2. 实例化和管理 Provider
3. 按 role 路由 chat() 调用到正确的 provider+model
4. 支持运行时 set_role() 热切换
"""

import os
import logging
import threading
from dataclasses import dataclass, field

import yaml

from spma.llm.providers.base import (
    ProviderConfig,
    RoleConfig,
    RetryConfig,
    LLMConfigError,
    LLMUnavailableError,
)
from spma.llm.providers import register, by_name
from spma.llm.providers.anthropic import AnthropicProvider
from spma.llm.providers.openai_compat import OpenAICompatProvider
from spma.llm.providers.local_vllm import LocalVLLMProvider

logger = logging.getLogger(__name__)

# ── Provider 工厂映射 ────────────────────────────────────────────────────────

_PROVIDER_FACTORIES = {
    "anthropic": AnthropicProvider,
    "openai_compat": OpenAICompatProvider,
}


@dataclass
class LLMConfig:
    """完整的 LLM 配置——从 YAML + 环境变量加载后得到。"""
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    retry: RetryConfig = field(default_factory=RetryConfig)


def _load_yaml_config(path: str) -> dict:
    """加载 YAML 配置文件。"""
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _resolve_env_var(value: str) -> str:
    """解析 ${VAR_NAME} 格式的环境变量引用。"""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        return os.environ.get(var_name, "")
    return value


def _apply_env_overrides(config: LLMConfig) -> LLMConfig:
    """应用环境变量覆盖——优先级：env var > yaml > 默认值。"""
    # 覆盖 provider 的 api_key 和 base_url
    for pname, pcfg in config.providers.items():
        env_key = f"SPMA_LLM_PROVIDER_{pname.upper()}_API_KEY"
        if env_key in os.environ:
            pcfg.api_key = os.environ[env_key]
        env_url = f"SPMA_LLM_PROVIDER_{pname.upper()}_BASE_URL"
        if env_url in os.environ:
            pcfg.base_url = os.environ[env_url]

    # 覆盖 role 的 provider 和 model
    for rname, rcfg in config.roles.items():
        env_provider = f"SPMA_LLM_ROLE_{rname.upper()}_PROVIDER"
        if env_provider in os.environ:
            rcfg.provider = os.environ[env_provider]
        env_model = f"SPMA_LLM_ROLE_{rname.upper()}_MODEL"
        if env_model in os.environ:
            rcfg.model = os.environ[env_model]

    return config


def load_llm_config(yaml_path: str) -> LLMConfig:
    """从 YAML 文件加载 LLM 配置，并应用环境变量覆盖。

    Args:
        yaml_path: config/spma.yaml 的路径

    Returns:
        LLMConfig 实例

    Raises:
        LLMConfigError: 配置不合法时抛出
    """
    raw = _load_yaml_config(yaml_path)
    llm_raw = raw.get("llm")
    if not llm_raw:
        raise LLMConfigError("缺少 'llm' 配置段")

    config = LLMConfig()

    # 1. 解析 providers
    providers_raw = llm_raw.get("providers", {})
    for name, pdata in providers_raw.items():
        config.providers[name] = ProviderConfig(
            type=pdata["type"],
            api_key=_resolve_env_var(pdata.get("api_key", "")),
            base_url=_resolve_env_var(pdata.get("base_url", "")),
            default_model=pdata.get("default_model"),
        )

    # 2. 解析 roles
    roles_raw = llm_raw.get("roles", {})
    if not roles_raw:
        raise LLMConfigError("至少需要配置 default role")

    for role_name, rdata in roles_raw.items():
        config.roles[role_name] = RoleConfig.from_dict(rdata)

    # 3. 确保存在 default role
    if "default" not in config.roles:
        first_provider = next(iter(config.providers.keys()))
        first_pcfg = config.providers[first_provider]
        config.roles["default"] = RoleConfig(
            provider=first_provider,
            model=first_pcfg.default_model or "",
        )

    # 4. 校验 role 引用的 provider 已注册
    for role_name, rcfg in config.roles.items():
        if rcfg.provider not in config.providers:
            raise LLMConfigError(
                f"Role '{role_name}' 引用的 provider '{rcfg.provider}' "
                f"未在 providers 中注册。可用: {list(config.providers.keys())}"
            )

    # 5. 解析 retry 配置
    retry_raw = llm_raw.get("retry", {})
    config.retry = RetryConfig(
        max_retries=retry_raw.get("max_retries", 3),
        multiplier_seconds=retry_raw.get("multiplier_seconds", 0.5),
        max_wait_seconds=retry_raw.get("max_wait_seconds", 2.0),
    )

    # 6. 应用环境变量覆盖
    config = _apply_env_overrides(config)

    return config
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_router.py::TestLLMConfigFromYAML -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/llm/router.py tests/unit/llm/test_router.py
git commit -m "feat: implement LLMConfig loader from YAML with env var overrides"
```

---

### Task 6: 实现 LLMRouter 路由和热切换

**Files:**
- Modify: `src/spma/llm/router.py` (追加 LLMRouter 类)
- Modify: `tests/unit/llm/test_router.py` (追加测试)

- [ ] **Step 1: 追加 LLMRouter 测试**

在 `tests/unit/llm/test_router.py` 末尾追加：

```python
from unittest.mock import AsyncMock, MagicMock, patch


class TestLLMRouter:
    @pytest.fixture
    def router(self):
        from spma.llm.router import LLMRouter, LLMConfig
        from spma.llm.providers.base import ProviderConfig, RoleConfig

        config = LLMConfig(
            providers={
                "mock_a": ProviderConfig(type="openai_compat", api_key="sk-a", base_url="https://a.test"),
                "mock_b": ProviderConfig(type="openai_compat", api_key="sk-b", base_url="https://b.test"),
            },
            roles={
                "classification": RoleConfig(provider="mock_a", model="fast-model"),
                "generation": RoleConfig(provider="mock_b", model="pro-model"),
                "default": RoleConfig(provider="mock_a", model="fast-model"),
                "fallback": RoleConfig(provider="mock_a", model="fallback-model"),
            },
        )
        return LLMRouter(config)

    def test_get_role_config(self, router):
        cfg = router.get_role_config("classification")
        assert cfg is not None
        assert cfg.provider == "mock_a"
        assert cfg.model == "fast-model"

    def test_get_role_config_unknown_returns_none(self, router):
        cfg = router.get_role_config("nonexistent")
        assert cfg is None

    def test_set_role_updates_config(self, router):
        router.set_role("classification", "mock_b", "new-fast-model")
        cfg = router.get_role_config("classification")
        assert cfg.provider == "mock_b"
        assert cfg.model == "new-fast-model"

    def test_set_role_unknown_provider_raises(self, router):
        with pytest.raises(LLMConfigError, match="未在 providers 中注册"):
            router.set_role("classification", "nonexistent", "model")

    def test_list_roles(self, router):
        roles = router.list_roles()
        assert "classification" in roles
        assert "generation" in roles
        assert roles["classification"].model == "fast-model"

    def test_list_providers(self, router):
        providers = router.list_providers()
        assert "mock_a" in providers
        assert "mock_b" in providers

    @pytest.mark.asyncio
    async def test_chat_routes_by_role(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="response from mock")
            mock_provider.ping = AsyncMock(return_value=True)
            provs.__getitem__.return_value = mock_provider

            result = await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="generation",
            )
            assert result == "response from mock"
            mock_provider.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_falls_back_to_default_role(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="default response")
            mock_provider.ping = AsyncMock(return_value=True)
            provs.__getitem__.return_value = mock_provider

            result = await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="nonexistent",
            )
            # 未注册的 role → 退回 default
            assert result == "default response"

    @pytest.mark.asyncio
    async def test_chat_with_explicit_model_overrides_role_model(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="custom model response")
            mock_provider.ping = AsyncMock(return_value=True)
            provs.__getitem__.return_value = mock_provider

            await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="generation",
                model="explicit-model",
            )
            call_kwargs = mock_provider.chat.call_args.kwargs
            assert call_kwargs["model"] == "explicit-model"

    @pytest.mark.asyncio
    async def test_chat_provider_unhealthy_falls_back(self, router):
        with patch.object(router, '_providers') as provs:
            main_provider = MagicMock()
            main_provider.chat = AsyncMock(side_effect=Exception("unhealthy"))
            main_provider.ping = AsyncMock(return_value=False)

            fallback_provider = MagicMock()
            fallback_provider.chat = AsyncMock(return_value="fallback response")
            fallback_provider.ping = AsyncMock(return_value=True)

            def get_provider(name):
                if name == "mock_b":
                    return main_provider
                return fallback_provider
            provs.__getitem__.side_effect = get_provider
            provs.__contains__.return_value = True

            result = await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="generation",
            )
            assert "fallback" in result

    def test_set_role_thread_safety(self, router):
        import threading
        import time

        errors = []

        def switcher():
            for i in range(50):
                try:
                    if i % 2 == 0:
                        router.set_role("classification", "mock_b", f"model-{i}")
                    else:
                        router.set_role("classification", "mock_a", f"model-{i}")
                except Exception as e:
                    errors.append(e)

        def reader():
            for _ in range(50):
                try:
                    cfg = router.get_role_config("classification")
                    assert cfg is not None
                    time.sleep(0.001)
                except Exception as e:
                    errors.append(e)

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=switcher))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/llm/test_router.py::TestLLMRouter -v
```
Expected: 全部 FAIL（`LLMRouter` 类尚未实现）

- [ ] **Step 3: 实现 LLMRouter 类**

在 `src/spma/llm/router.py` 末尾追加：

```python
class LLMRouter:
    """线程安全的 LLM 路由单例。

    负责：
    1. 从 LLMConfig 实例化所有 Provider
    2. 维护 role → (provider, model) 映射
    3. 按 role 路由 chat() 调用
    4. 支持运行时 set_role() 热切换
    """

    _instance: "LLMRouter | None" = None

    def __init__(self, config: LLMConfig):
        self._lock = threading.RLock()
        self._config = config
        self._roles: dict[str, RoleConfig] = dict(config.roles)
        self._providers: dict[str, "LLMProvider"] = {}

        # 实例化所有 provider 并注册到全局表
        for pname, pcfg in config.providers.items():
            factory = _PROVIDER_FACTORIES.get(pcfg.type)
            if factory is None:
                raise LLMConfigError(f"未知的 provider 类型: {pcfg.type}")
            provider = factory(pname, pcfg)
            self._providers[pname] = provider
            register(pname, provider)

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        *,
        role: str | None = None,
        model: str | None = None,
        **kwargs,
    ) -> str:
        """核心路由方法：按 role 将请求路由到正确的 provider+model。

        路由优先级：
        1. 传入 model → 当前 role 的 provider + 传入的 model
        2. role 配置的 provider + model
        3. role 未注册 → 退回 default role
        4. provider 不健康 → 降级到 fallback role
        """
        role_name = role or "default"

        with self._lock:
            role_cfg = self._roles.get(role_name)
            if role_cfg is None:
                role_cfg = self._roles.get("default")
                if role_cfg is None:
                    raise LLMConfigError(f"Role '{role_name}' 未配置且无 default role")

            provider_name = role_cfg.provider
            resolved_model = model or role_cfg.model
            resolved_kwargs = {
                "max_tokens": role_cfg.max_tokens,
                "temperature": role_cfg.temperature,
            }
            if role_cfg.thinking:
                resolved_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
            resolved_kwargs.update(role_cfg.extra_kwargs)
            resolved_kwargs.update(kwargs)

        provider = self._providers.get(provider_name)
        if provider is None:
            raise LLMConfigError(f"Provider '{provider_name}' 不存在")

        try:
            return await provider.chat(messages, resolved_model, **resolved_kwargs)
        except Exception as e:
            logger.warning(f"Provider '{provider_name}' 调用失败: {e}")

        # 降级到 fallback
        fallback_cfg = self._roles.get("fallback")
        if fallback_cfg and fallback_cfg.provider != provider_name:
            fb_provider = self._providers.get(fallback_cfg.provider)
            if fb_provider:
                logger.info(f"降级到 fallback: {fallback_cfg.provider}/{fallback_cfg.model}")
                try:
                    return await fb_provider.chat(messages, fallback_cfg.model)
                except Exception as e2:
                    raise LLMUnavailableError(
                        f"fallback provider '{fallback_cfg.provider}' 也失败: {e2}", cause=e2
                    )
        raise LLMUnavailableError(f"Provider '{provider_name}' 不可用且无可用 fallback")

    def set_role(self, role: str, provider: str, model: str, **kwargs) -> None:
        """运行时热切换——原子替换某个 role 的 (provider, model)。

        Args:
            role: 角色名称
            provider: 已注册的 provider 名称
            model: 模型名称
            **kwargs: 覆盖 role 的其他参数（max_tokens, temperature 等）
        """
        if provider not in self._providers:
            raise LLMConfigError(
                f"Provider '{provider}' 未在 providers 中注册。可用: {list(self._providers.keys())}"
            )

        with self._lock:
            old_cfg = self._roles.get(role)
            self._roles[role] = RoleConfig(provider=provider, model=model, **kwargs)

        logger.info(
            f"Role '{role}' 热切换: {old_cfg.provider}/{old_cfg.model if old_cfg else 'N/A'} "
            f"→ {provider}/{model}"
        )

    def get_role_config(self, role: str) -> RoleConfig | None:
        """查询当前 role 配置（只读）。"""
        with self._lock:
            cfg = self._roles.get(role)
            return RoleConfig(
                provider=cfg.provider,
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                thinking=cfg.thinking,
            ) if cfg else None

    def list_roles(self) -> dict[str, RoleConfig]:
        """返回所有 role 的当前配置。"""
        with self._lock:
            return {
                name: RoleConfig(
                    provider=cfg.provider,
                    model=cfg.model,
                    max_tokens=cfg.max_tokens,
                    temperature=cfg.temperature,
                    thinking=cfg.thinking,
                )
                for name, cfg in self._roles.items()
            }

    def list_providers(self) -> dict[str, str]:
        """返回所有已注册 provider 名称→类型。"""
        return {name: p.name for name, p in self._providers.items()}

    def get_langchain_client(self, role: str | None = None):
        """返回指定 role 对应的 LangChain 客户端。

        供 LangGraph StateGraph 需要 BaseChatModel 的场景使用。
        """
        role_name = role or "default"
        with self._lock:
            cfg = self._roles.get(role_name) or self._roles["default"]
            provider = self._providers[cfg.provider]
        return provider.get_langchain_client(cfg.model)

    # ── 单例管理 ──────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "LLMRouter":
        if cls._instance is None:
            raise RuntimeError("LLMRouter 未初始化，请先调用 LLMRouter.initialize()")
        return cls._instance

    @classmethod
    def initialize(cls, yaml_path: str) -> "LLMRouter":
        """启动时调用：加载配置并初始化全局单例。"""
        config = load_llm_config(yaml_path)
        cls._instance = cls(config)
        logger.info(
            f"LLMRouter 初始化完成，providers={list(config.providers.keys())}, "
            f"roles={list(config.roles.keys())}"
        )
        return cls._instance
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/llm/test_router.py::TestLLMRouter -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/llm/router.py tests/unit/llm/test_router.py
git commit -m "feat: implement LLMRouter with role-based routing and hot-swap"
```

---

### Task 7: 改造 llm/__init__.py 和 clients.py（向后兼容层）

**Files:**
- Modify: `src/spma/llm/__init__.py`
- Modify: `src/spma/llm/clients.py`

- [ ] **Step 1: 改造 `__init__.py`——从 router 导出**

```python
# src/spma/llm/__init__.py
"""LLM 抽象层——多提供商路由。

通过 LLMRouter 单例统一管理所有 provider 和 role 配置。
模型分层:
- 高速路径 (<500ms): classification role → 意图分类/实体抽取/完备度判断
- 质量路径 (<2s): generation role → 回答生成/SQL 生成/复杂推理
- 降级路径: fallback role → 全部 LLM 不可用时的兜底

使用方式:
    from spma.llm import chat, get_langchain_client

    reply = await chat(messages, role="generation")
    llm = get_langchain_client(role="classification")
"""

from spma.llm.router import LLMRouter


async def chat(messages: list[dict], *, role: str = "default", model: str | None = None, **kwargs) -> str:
    """异步调用 LLM 完成对话（通过 router 路由）。

    Args:
        messages: 消息列表 [{"role": "...", "content": "..."}]
        role: 角色槽位名称（classification/generation/completeness/default）
        model: 显式指定模型名，覆盖 role 配置中的默认模型
        **kwargs: 其他参数传递给 provider

    Returns:
        LLM 响应文本
    """
    router = LLMRouter.get_instance()
    return await router.chat(messages, role=role, model=model, **kwargs)


def get_langchain_client(role: str = "default"):
    """返回指定 role 对应的 LangChain ChatModel。

    供 LangGraph StateGraph 等需要 BaseChatModel 的场景。
    """
    router = LLMRouter.get_instance()
    return router.get_langchain_client(role)
```

- [ ] **Step 2: 改造 `clients.py`——标记废弃，委托给 router**

```python
# src/spma/llm/clients.py
"""⚠️ DEPRECATED: 此模块已废弃，请使用 spma.llm 模块。

保留此文件仅为向后兼容。所有调用委托给 LLMRouter。
"""

import warnings
from spma.llm import chat as _router_chat, get_langchain_client as _router_get_client


def get_default_llm():
    """获取默认 LLM 客户端（LangChain ChatAnthropic）。

    ⚠️ DEPRECATED: 请使用 spma.llm.get_langchain_client(role="default")
    """
    warnings.warn(
        "get_default_llm() 已废弃，请使用 spma.llm.get_langchain_client(role='default')",
        DeprecationWarning,
        stacklevel=2,
    )
    return _router_get_client("default")


async def chat(messages: list[dict], model: str | None = None, **kwargs) -> str:
    """异步调用 LLM 完成对话。

    ⚠️ DEPRECATED: 请使用 spma.llm.chat(messages, role='default', model=...)
    """
    warnings.warn(
        "llm.clients.chat() 已废弃，请使用 spma.llm.chat(messages, role='default')",
        DeprecationWarning,
        stacklevel=2,
    )
    return await _router_chat(messages, role="default", model=model, **kwargs)
```

- [ ] **Step 3: 验证向后兼容——确保现有调用方式仍然可用**

```bash
uv run python -c "
from spma.llm import chat, get_langchain_client
print('New API imported OK')
from spma.llm.clients import chat as old_chat, get_default_llm as old_get
print('Old API (deprecated) imported OK')
"
```
Expected: 输出两条 OK（可能有 DeprecationWarning）

- [ ] **Step 4: 运行全部单元测试确保无回归**

```bash
uv run pytest tests/unit/llm/ -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/llm/__init__.py src/spma/llm/clients.py
git commit -m "refactor: redirect llm/__init__.py and clients.py through LLMRouter"
```

---

### Task 8: 改造 config/spma.yaml

**Files:**
- Modify: `config/spma.yaml`

- [ ] **Step 1: 替换 `llm` 配置段**

在 `config/spma.yaml` 中，将现有 `llm:` 段（约第 36-44 行）替换为：

```yaml
  llm:
    providers:
      anthropic:
        type: anthropic
        api_key: "${ANTHROPIC_API_KEY}"
        base_url: "https://api.anthropic.com"
        default_model: "claude-sonnet-4-6"

      deepseek:
        type: openai_compat
        api_key: "${DEEPSEEK_API_KEY}"
        base_url: "https://api.deepseek.com"
        default_model: "deepseek-v4-pro"

      openai:
        type: openai_compat
        api_key: "${OPENAI_API_KEY}"
        base_url: "https://api.openai.com/v1"
        default_model: "gpt-4o"

      local_vllm:
        type: openai_compat
        api_key: "not-needed"
        base_url: "http://vllm.internal:8000/v1"
        default_model: "qwen3-8b-local"

    roles:
      classification:
        provider: deepseek
        model: deepseek-v4-flash
        max_tokens: 2048
        temperature: 0.1

      generation:
        provider: deepseek
        model: deepseek-v4-pro
        max_tokens: 4096
        temperature: 0.3
        thinking: enabled

      completeness:
        provider: deepseek
        model: deepseek-v4-flash
        max_tokens: 1024
        temperature: 0.1

      default:
        provider: deepseek
        model: deepseek-v4-pro

      fallback:
        provider: local_vllm
        model: qwen3-8b-local

    retry:
      max_retries: 3
      multiplier_seconds: 0.5
      max_wait_seconds: 2.0
```

- [ ] **Step 2: 验证配置可正常加载**

```bash
uv run python -c "
from spma.llm.router import load_llm_config
import os
config = load_llm_config(os.path.join('config', 'spma.yaml'))
print('Providers:', list(config.providers.keys()))
print('Roles:', list(config.roles.keys()))
print('Generation provider:', config.roles['generation'].provider, config.roles['generation'].model)
"
```
Expected: 输出 4 个 provider、5 个 role、generation role 指向 deepseek/deepseek-v4-pro

- [ ] **Step 3: 提交**

```bash
git add config/spma.yaml
git commit -m "config: restructure llm section for multi-provider support"
```

---

### Task 9: 移除 constants.py 中的硬编码模型名

**Files:**
- Modify: `src/spma/config/constants.py`

- [ ] **Step 1: 移除硬编码模型常量**

编辑 `src/spma/config/constants.py`，删除第 23-25 行：

```python
# 删除这三行：
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_LOCAL_FALLBACK = "qwen3-8b-local"
```

- [ ] **Step 2: 确认无代码引用这些常量**

```bash
grep -rn "MODEL_HAIKU\|MODEL_SONNET\|MODEL_LOCAL_FALLBACK" src/ --include="*.py"
```
Expected: 无输出（或仅在 constants.py 自身）

- [ ] **Step 3: 提交**

```bash
git add src/spma/config/constants.py
git commit -m "refactor: remove hardcoded model name constants from constants.py"
```

---

### Task 10: 改造 query.py 使用 router

**Files:**
- Modify: `src/spma/api/routes/query.py`

- [ ] **Step 1: 替换 get_default_llm 调用**

在 `src/spma/api/routes/query.py` 中：

将第 55-57 行：
```python
from spma.llm.clients import get_default_llm

llm = get_default_llm()
```

改为：
```python
from spma.llm import get_langchain_client

llm = get_langchain_client(role="generation")
```

将内部 `graph()` 构造中的 LLM 调用（约 118-120 行、133-137 行）——这些使用 `build_doc_agent_graph(llm)` 和 `build_code_agent_graph(llm)`，其中 `llm` 已经是 LangChain 客户端，无需额外修改。

- [ ] **Step 2: 验证导入无报错**

```bash
uv run python -c "from spma.api.routes.query import router; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 3: 提交**

```bash
git add src/spma/api/routes/query.py
git commit -m "refactor: use router.get_langchain_client in query route"
```

---

### Task 11: 改造 SQL Agent 的 LLM 调用

**Files:**
- Modify: `src/spma/agents/sql/generator.py`
- Modify: `src/spma/agents/sql/verifier.py`

- [ ] **Step 1: 改造 generator.py**

在 `src/spma/agents/sql/generator.py` 中：

将第 54 行：
```python
from spma.llm.clients import chat
```

改为：
```python
from spma.llm import chat
```

将第 61-63 行中的 `model="claude-sonnet-4-20250514"` 移除，改为通过 `role` 路由：

```python
response = await llm_client(
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    # 移除硬编码 model="claude-sonnet-4-20250514"
    # role 在 generate_node 函数签名中传递，或默认用 generation role
)
```

- [ ] **Step 2: 改造 verifier.py**

同理，在 `src/spma/agents/sql/verifier.py` 中：

将第 106 行：
```python
from spma.llm.clients import chat
```

改为：
```python
from spma.llm import chat
```

- [ ] **Step 3: 验证 SQL Agent 模块可导入**

```bash
uv run python -c "from spma.agents.sql.generator import generate_node; from spma.agents.sql.verifier import verify_node; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/sql/generator.py src/spma/agents/sql/verifier.py
git commit -m "refactor: use role-based routing in SQL agent"
```

---

### Task 12: 改造 L1 降级动作

**Files:**
- Modify: `src/spma/infrastructure/degradation/actions/l1_llm.py`

- [ ] **Step 1: 重写 L1LLMDegradation——移除硬编码模型名**

```python
# src/spma/infrastructure/degradation/actions/l1_llm.py
"""L1: 主 LLM 不可用 → 切换到 fallback role 的 provider+model。

动态获取当前 generation role 的配置，降级时切到 fallback role，
恢复时切回原 provider+model。
"""

import logging
import time
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L1LLMDegradation(DegradationAction):
    """L1 LLM 降级：主模型→fallback 模型（动态读取 role 配置）。"""

    level: DegradationLevel = "L1"

    def __init__(self, required_consecutive_pings: int = 3,
                 min_recovery_interval_seconds: float = 60.0):
        self.is_active = False
        self._required_pings = required_consecutive_pings
        self._min_interval = min_recovery_interval_seconds
        self._consecutive_ok = 0
        self._last_check_time = 0.0
        self._original_provider: str | None = None
        self._original_model: str | None = None

    async def health_check(self) -> bool:
        """检查当前 generation role 的 provider 是否可用。"""
        from spma.llm.router import LLMRouter

        self._last_check_time = time.time()
        try:
            router = LLMRouter.get_instance()
            gen_cfg = router.get_role_config("generation")
            if gen_cfg is None:
                return True

            provider = router._providers.get(gen_cfg.provider)
            if provider is None:
                return False

            ok = await provider.ping()
            if ok:
                self._consecutive_ok += 1
            else:
                self._consecutive_ok = 0
            return ok
        except Exception:
            self._consecutive_ok = 0
            return False

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return

        from spma.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        gen_cfg = router.get_role_config("generation")
        fallback_cfg = router.get_role_config("fallback")

        if gen_cfg is None or fallback_cfg is None:
            logger.warning("L1 降级跳过: generation 或 fallback role 未配置")
            return

        # 保存原始配置用于恢复
        self._original_provider = gen_cfg.provider
        self._original_model = gen_cfg.model

        # 执行切换
        router.set_role("generation", fallback_cfg.provider, fallback_cfg.model)
        self.is_active = True
        logger.warning(
            f"L1 降级触发: {reason}，generation {self._original_provider}/{self._original_model} "
            f"→ {fallback_cfg.provider}/{fallback_cfg.model}"
        )

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        if self._original_provider is None or self._original_model is None:
            return False

        from spma.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        router.set_role("generation", self._original_provider, self._original_model)
        self.is_active = False
        logger.info(
            f"L1 恢复: generation 切回 {self._original_provider}/{self._original_model}"
        )
        return True

    def recovery_conditions_met(self) -> bool:
        return (
            self._consecutive_ok >= self._required_pings
            and (time.time() - self._last_check_time) >= self._min_interval
        )

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
```

- [ ] **Step 2: 更新 bootstrap.py 中 L1 的实例化**

在 `src/spma/bootstrap.py` 第 68-69 行，L1 不再需要传入 `llm_client`：

```python
# 改为（移除 llm_client 参数）：
actions.append(L1LLMDegradation())
```

- [ ] **Step 3: 验证降级模块可导入**

```bash
uv run python -c "from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 4: 提交**

```bash
git add src/spma/infrastructure/degradation/actions/l1_llm.py src/spma/bootstrap.py
git commit -m "refactor: make L1 degradation dynamic via router role hot-swap"
```

---

### Task 13: 新增 Admin API 端点——LLM 热切换

**Files:**
- Create: `src/spma/api/routes/llm_admin.py`
- Modify: `src/spma/api/app.py`

- [ ] **Step 1: 创建 llm_admin 路由模块**

```python
# src/spma/api/routes/llm_admin.py
"""LLM 管理端点——运行时查询和热切换 router 配置。

POST /api/v1/admin/llm/role/{role_name}  — 热切换 role 的 provider/model
GET  /api/v1/admin/llm/roles              — 查询所有 role 当前配置
GET  /api/v1/admin/llm/providers          — 查询所有 provider 状态
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from spma.llm.router import LLMRouter
from spma.llm.providers.base import LLMConfigError

logger = logging.getLogger(__name__)

router = APIRouter()


class RoleSwitchRequest(BaseModel):
    provider: str
    model: str


class RoleConfigResponse(BaseModel):
    provider: str
    model: str
    max_tokens: int
    temperature: float
    thinking: str | None = None


@router.post("/api/v1/admin/llm/role/{role_name}")
async def switch_role(role_name: str, body: RoleSwitchRequest):
    """热切换指定 role 的 provider/model——零延迟生效。"""
    try:
        router_instance = LLMRouter.get_instance()
        router_instance.set_role(role_name, body.provider, body.model)
        new_cfg = router_instance.get_role_config(role_name)
        return {
            "status": "ok",
            "role": role_name,
            "current": {
                "provider": new_cfg.provider,
                "model": new_cfg.model,
            },
        }
    except LLMConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/admin/llm/roles")
async def list_roles():
    """查询所有 role 的当前配置。"""
    try:
        router_instance = LLMRouter.get_instance()
        roles = router_instance.list_roles()
        return {
            role_name: {
                "provider": cfg.provider,
                "model": cfg.model,
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "thinking": cfg.thinking,
            }
            for role_name, cfg in roles.items()
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/admin/llm/providers")
async def list_providers():
    """查询所有已注册 provider 及健康状态。"""
    try:
        router_instance = LLMRouter.get_instance()
        providers = router_instance.list_providers()
        result = {}
        for pname in providers:
            provider = router_instance._providers.get(pname)
            healthy = await provider.ping() if provider else False
            result[pname] = {
                "type": provider.name if provider else "unknown",
                "healthy": healthy,
            }
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
```

- [ ] **Step 2: 在 app.py 中注册路由**

在 `src/spma/api/app.py` 中：

在 import 区域追加：
```python
from spma.api.routes.llm_admin import router as llm_admin_router
```

在 `create_app()` 函数中（在 `app.include_router(query_router)` 之后）追加：
```python
app.include_router(llm_admin_router)
```

- [ ] **Step 3: 验证 app 可导入**

```bash
uv run python -c "from spma.api.app import create_app; app = create_app(); print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 4: 提交**

```bash
git add src/spma/api/routes/llm_admin.py src/spma/api/app.py
git commit -m "feat: add Admin API endpoints for LLM role hot-swap and provider listing"
```

---

### Task 14: 启动时初始化 LLMRouter

**Files:**
- Modify: `src/spma/api/app.py`

- [ ] **Step 1: 在 create_app 中添加 startup 事件初始化 router**

在 `create_app()` 函数的 return 前追加：

```python
@app.on_event("startup")
async def startup_llm_router():
    """启动时初始化 LLMRouter 单例。"""
    import os
    from spma.llm.router import LLMRouter

    yaml_path = os.environ.get(
        "SPMA_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "spma.yaml"),
    )
    LLMRouter.initialize(os.path.abspath(yaml_path))
```

- [ ] **Step 2: 验证路径正确**

```bash
uv run python -c "
import os
yaml_path = os.path.join(os.path.dirname('src/spma/api/app.py'), '..', '..', 'config', 'spma.yaml')
print('Calculated path:', os.path.abspath(yaml_path))
print('Exists:', os.path.exists(os.path.abspath(yaml_path)))
"
```

- [ ] **Step 3: 提交**

```bash
git add src/spma/api/app.py
git commit -m "feat: initialize LLMRouter on app startup"
```

---

### Task 15: 更新 conftest.py 的 mock_llm fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: 实现 mock_llm fixture**

```python
# tests/conftest.py
"""全局测试 fixtures。"""

import pytest
from unittest.mock import AsyncMock, MagicMock


class MockLLMResponse:
    """可预编排的 Mock LLM 响应序列。"""

    def __init__(self, response_texts: list[str] | None = None):
        self._responses = response_texts or ["Mock response"]
        self._index = 0
        self.calls: list[dict] = []

    def next(self):
        result = self._responses[self._index % len(self._responses)]
        self._index += 1
        return result


@pytest.fixture
def mock_llm():
    """MockLLM fixture——按预编排响应序列逐轮返回。"""
    mock = AsyncMock()
    mock.chat = AsyncMock(return_value="Mock LLM response")
    mock.ping = AsyncMock(return_value=True)
    mock.supports_thinking = MagicMock(return_value=False)
    return mock


@pytest.fixture
def mock_router(mock_llm):
    """Mock LLMRouter fixture——所有 role 路由到同一个 mock provider。"""
    from spma.llm.router import LLMRouter, LLMConfig
    from spma.llm.providers.base import ProviderConfig, RoleConfig

    config = LLMConfig(
        providers={
            "mock": ProviderConfig(type="openai_compat", api_key="sk-test", base_url="https://mock.test"),
        },
        roles={
            "classification": RoleConfig(provider="mock", model="fast-model"),
            "generation": RoleConfig(provider="mock", model="pro-model"),
            "completeness": RoleConfig(provider="mock", model="fast-model"),
            "default": RoleConfig(provider="mock", model="pro-model"),
            "fallback": RoleConfig(provider="mock", model="fallback-model"),
        },
    )
    router = LLMRouter(config)
    # 替换 provider 为 mock
    router._providers["mock"] = mock_llm
    return router
```

- [ ] **Step 2: 验证 fixture 可用**

```bash
uv run python -c "from tests.conftest import mock_llm, mock_router; print('OK')"
```
Expected: 输出 `OK`

- [ ] **Step 3: 提交**

```bash
git add tests/conftest.py
git commit -m "test: implement mock_llm and mock_router fixtures"
```

---

### Task 16: 运行全部测试并修复回归

**Files:** 无特定文件——验证阶段

- [ ] **Step 1: 运行全部单元测试**

```bash
uv run pytest tests/unit/ -v
```
Expected: 全部 PASS

- [ ] **Step 2: 运行全部测试**

```bash
uv run pytest tests/ -v -k "not integration and not e2e"
```
Expected: 全部 PASS（集成和 E2E 测试需要外部服务，跳过）

- [ ] **Step 3: 检查 linter**

```bash
uv run ruff check src/spma/llm/
```
Expected: 无错误

- [ ] **Step 4: 修复所有发现的问题后提交**

```bash
git add -A
git commit -m "test: fix all regressions from multi-provider refactor"
```

---

### Task 17: 更新 feature_flags.yaml 支持 LLM 覆盖

**Files:**
- Modify: `config/feature_flags.yaml`

- [ ] **Step 1: 追加 llm_role_overrides 段**

在 `config/feature_flags.yaml` 末尾追加：

```yaml
  # LLM 角色覆盖——运行时切换 provider/model，与 router.set_role() 联动
  llm_role_overrides: {}
  # 示例:
  # llm_role_overrides:
  #   generation:
  #     provider: anthropic
  #     model: claude-sonnet-4-6
```

- [ ] **Step 2: 提交**

```bash
git add config/feature_flags.yaml
git commit -m "config: add llm_role_overrides section to feature_flags.yaml"
```

---

### Task 18: 端到端验证

- [ ] **Step 1: 启动应用并验证 Admin API**

```bash
# 在另一个终端中启动
uv run spma-api &
sleep 3

# 测试 provider 列表
curl -s http://localhost:8000/api/v1/admin/llm/providers | python -m json.tool

# 测试 role 列表
curl -s http://localhost:8000/api/v1/admin/llm/roles | python -m json.tool

# 热切换 generation role 到 anthropic
curl -s -X POST http://localhost:8000/api/v1/admin/llm/role/generation \
  -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "model": "claude-sonnet-4-6"}' | python -m json.tool

# 验证切换生效
curl -s http://localhost:8000/api/v1/admin/llm/roles | python -m json.tool
```

- [ ] **Step 2: 验证查询端点仍然工作**

```bash
curl -s -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "测试查询"}' | python -m json.tool
```

- [ ] **Step 3: 关闭应用并提交最终版本**

```bash
kill %1
git add -A
git commit -m "feat: complete multi-provider LLM abstraction layer implementation"
```
