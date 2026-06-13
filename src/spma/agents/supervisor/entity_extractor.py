"""实体抽取——与意图分类共享 LLM 调用，一次请求同时输出。

设计依据: SPMA-design-01 第六节 实体抽取设计
"""

from spma.models.entities import ExtractedEntities


async def extract_entities(
    user_query: str, classification: dict
) -> ExtractedEntities:
    """从用户问题中抽取 12 种结构化实体。

    Slice 1: 返回默认空实体（LLM 抽取待后续实现）。
    """
    return ExtractedEntities(
        module=None,
        req_ids=[],
        time_range=None,
        version=None,
        table_names=classification.get("entities", {}).get("table_names", []),
        column_names=classification.get("entities", {}).get("column_names", []),
        metrics=classification.get("entities", {}).get("metrics", []),
        group_by=None,
        code_refs=classification.get("entities", {}).get("code_refs", []),
        person=None,
        doc_types=classification.get("entities", {}).get("doc_types", []),
    )
