"""⚠️ DEPRECATED: 此模块已废弃，请使用 spma.llm 模块。"""

import warnings
from spma.llm import chat as _router_chat, get_langchain_client as _router_get_client


def get_default_llm():
    """获取默认 LLM 客户端。⚠️ DEPRECATED"""
    warnings.warn(
        "get_default_llm() 已废弃，请使用 spma.llm.get_langchain_client(role='default')",
        DeprecationWarning, stacklevel=2,
    )
    return _router_get_client("default")


async def chat(messages: list[dict], model: str | None = None, **kwargs) -> str:
    """异步调用 LLM 完成对话。⚠️ DEPRECATED"""
    warnings.warn(
        "llm.clients.chat() 已废弃，请使用 spma.llm.chat(messages, role='default')",
        DeprecationWarning, stacklevel=2,
    )
    return await _router_chat(messages, role="default", model=model, **kwargs)
