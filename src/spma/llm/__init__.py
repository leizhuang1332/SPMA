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
    """异步调用 LLM 完成对话（通过 router 路由）。"""
    router = LLMRouter.get_instance()
    return await router.chat(messages, role=role, model=model, **kwargs)


def get_langchain_client(role: str = "default"):
    """返回指定 role 对应的 LangChain ChatModel。"""
    router = LLMRouter.get_instance()
    return router.get_langchain_client(role)
