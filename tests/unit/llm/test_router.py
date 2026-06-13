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
    local_vllm:
      type: openai_compat
      api_key: not-needed
      base_url: http://localhost:8000/v1
      default_model: qwen3-8b-local
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

        assert len(config.providers) == 3
        assert config.providers["deepseek"].type == "openai_compat"
        assert config.roles["classification"].max_tokens == 2048
        assert config.roles["generation"].thinking == "enabled"
        assert config.roles["fallback"].provider == "local_vllm"
        assert config.retry.max_retries == 5

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

        assert "default" in config.roles
        assert config.roles["default"].provider is not None


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

    def test_list_providers(self, router):
        providers = router.list_providers()
        assert "mock_a" in providers
        assert "mock_b" in providers

    @pytest.mark.asyncio
    async def test_chat_routes_by_role(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="response from mock")
            provs.__getitem__.return_value = mock_provider

            result = await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="generation",
            )
            assert result == "response from mock"

    @pytest.mark.asyncio
    async def test_chat_falls_back_to_default_role(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="default response")
            provs.__getitem__.return_value = mock_provider

            result = await router.chat(
                [{"role": "user", "content": "Hello"}],
                role="nonexistent",
            )
            assert result == "default response"

    @pytest.mark.asyncio
    async def test_chat_with_explicit_model_overrides_role_model(self, router):
        with patch.object(router, '_providers') as provs:
            mock_provider = MagicMock()
            mock_provider.chat = AsyncMock(return_value="custom model response")
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

            fallback_provider = MagicMock()
            fallback_provider.chat = AsyncMock(return_value="fallback response")

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
