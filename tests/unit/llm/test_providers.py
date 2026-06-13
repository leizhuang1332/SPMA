# tests/unit/llm/test_providers.py
"""Provider 基类和异常类单元测试。"""

import pytest
from spma.llm.providers.base import (
    LLMProvider,
    RoleConfig,
    ProviderConfig,
    RetryConfig,
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
        cfg = ProviderConfig(
            type="openai_compat", api_key="sk-xxx", base_url="https://api.example.com"
        )
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


class TestRetryConfig:
    def test_default_values(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.multiplier_seconds == 0.5
        assert cfg.max_wait_seconds == 2.0

    def test_custom_values(self):
        cfg = RetryConfig(max_retries=5, multiplier_seconds=1.0, max_wait_seconds=10.0)
        assert cfg.max_retries == 5
        assert cfg.multiplier_seconds == 1.0
        assert cfg.max_wait_seconds == 10.0


class TestRoleConfigFromDict:
    def test_from_dict_extracts_known_fields(self):
        data = {"provider": "deepseek", "model": "deepseek-v4-pro", "max_tokens": 2048, "temperature": 0.1, "thinking": "enabled"}
        cfg = RoleConfig.from_dict(data)
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-v4-pro"
        assert cfg.max_tokens == 2048
        assert cfg.temperature == 0.1
        assert cfg.thinking == "enabled"

    def test_from_dict_unknown_fields_go_to_extra_kwargs(self):
        data = {"provider": "test", "model": "test-model", "custom_param": "custom_value", "another_param": 123}
        cfg = RoleConfig.from_dict(data)
        assert cfg.extra_kwargs == {"custom_param": "custom_value", "another_param": 123}

    def test_from_dict_missing_optional_fields(self):
        data = {"provider": "test", "model": "test-model"}
        cfg = RoleConfig.from_dict(data)
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.3
        assert cfg.thinking is None
        assert cfg.extra_kwargs == {}


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
