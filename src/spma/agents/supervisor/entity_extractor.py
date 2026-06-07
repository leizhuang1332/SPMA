"""实体抽取——与意图分类共享 LLM 调用，一次请求同时输出。

设计依据: SPMA-design-01 第六节 实体抽取设计
"""

from spma.models.entities import ExtractedEntities


async def extract_entities(
    user_query: str, classification: dict
) -> ExtractedEntities:
    """从用户问题中抽取 12 种结构化实体。"""
    raise NotImplementedError
