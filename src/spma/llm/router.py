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
    for pname, pcfg in config.providers.items():
        env_key = f"SPMA_LLM_PROVIDER_{pname.upper()}_API_KEY"
        if env_key in os.environ:
            pcfg.api_key = os.environ[env_key]
        env_url = f"SPMA_LLM_PROVIDER_{pname.upper()}_BASE_URL"
        if env_url in os.environ:
            pcfg.base_url = os.environ[env_url]

    for rname, rcfg in config.roles.items():
        env_provider = f"SPMA_LLM_ROLE_{rname.upper()}_PROVIDER"
        if env_provider in os.environ:
            rcfg.provider = os.environ[env_provider]
        env_model = f"SPMA_LLM_ROLE_{rname.upper()}_MODEL"
        if env_model in os.environ:
            rcfg.model = os.environ[env_model]

    return config


def load_llm_config(yaml_path: str) -> LLMConfig:
    """从 YAML 文件加载 LLM 配置，并应用环境变量覆盖。"""
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


class LLMRouter:
    """线程安全的 LLM 路由单例。"""

    _instance: "LLMRouter | None" = None

    def __init__(self, config: LLMConfig):
        self._lock = threading.RLock()
        self._config = config
        self._roles: dict[str, RoleConfig] = dict(config.roles)
        self._providers: dict[str, "LLMProvider"] = {}

        for pname, pcfg in config.providers.items():
            factory = _PROVIDER_FACTORIES.get(pcfg.type)
            if factory is None:
                raise LLMConfigError(f"未知的 provider 类型: {pcfg.type}")
            provider = factory(pname, pcfg)
            self._providers[pname] = provider
            register(pname, provider)

    async def chat(
        self, messages: list[dict], *, role: str | None = None,
        model: str | None = None, **kwargs,
    ) -> str:
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

        try:
            provider = self._providers[provider_name]
        except KeyError:
            raise LLMConfigError(f"Provider '{provider_name}' 不存在")

        try:
            return await provider.chat(messages, model=resolved_model, **resolved_kwargs)
        except Exception as e:
            logger.warning(f"Provider '{provider_name}' 调用失败: {e}")

        # 降级到 fallback
        fallback_cfg = self._roles.get("fallback")
        if fallback_cfg and fallback_cfg.provider != provider_name:
            try:
                fb_provider = self._providers[fallback_cfg.provider]
            except KeyError:
                fb_provider = None
            if fb_provider:
                logger.info(f"降级到 fallback: {fallback_cfg.provider}/{fallback_cfg.model}")
                try:
                    return await fb_provider.chat(messages, model=fallback_cfg.model)
                except Exception as e2:
                    raise LLMUnavailableError(
                        f"fallback provider '{fallback_cfg.provider}' 也失败: {e2}", cause=e2
                    )
        raise LLMUnavailableError(f"Provider '{provider_name}' 不可用且无可用 fallback")

    def set_role(self, role: str, provider: str, model: str, **kwargs) -> None:
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
        with self._lock:
            cfg = self._roles.get(role)
            return RoleConfig(
                provider=cfg.provider, model=cfg.model,
                max_tokens=cfg.max_tokens, temperature=cfg.temperature,
                thinking=cfg.thinking,
            ) if cfg else None

    def list_roles(self) -> dict[str, RoleConfig]:
        with self._lock:
            return {
                name: RoleConfig(
                    provider=cfg.provider, model=cfg.model,
                    max_tokens=cfg.max_tokens, temperature=cfg.temperature,
                    thinking=cfg.thinking,
                )
                for name, cfg in self._roles.items()
            }

    def list_providers(self) -> dict[str, str]:
        return {name: p.name for name, p in self._providers.items()}

    def get_langchain_client(self, role: str | None = None):
        role_name = role or "default"
        with self._lock:
            cfg = self._roles.get(role_name) or self._roles["default"]
            provider = self._providers[cfg.provider]
        return provider.get_langchain_client(cfg.model)

    @classmethod
    def get_instance(cls) -> "LLMRouter":
        if cls._instance is None:
            raise RuntimeError("LLMRouter 未初始化，请先调用 LLMRouter.initialize()")
        return cls._instance

    @classmethod
    def initialize(cls, yaml_path: str) -> "LLMRouter":
        config = load_llm_config(yaml_path)
        cls._instance = cls(config)
        logger.info(
            f"LLMRouter 初始化完成，providers={list(config.providers.keys())}, "
            f"roles={list(config.roles.keys())}"
        )
        return cls._instance
