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
    router._providers["mock"] = mock_llm
    return router
