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
