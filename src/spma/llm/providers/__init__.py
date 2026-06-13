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
