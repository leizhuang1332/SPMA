"""Doc Agent 混合检索——BM25 + BGE-M3 向量检索 + RRF 融合。

分层权重: precise(BM25主导) / semantic(向量主导) / hybrid(等权)

设计依据: SPMA-design-02 §1 检索策略
"""

from spma.models.entities import WorkerEntities


def route_retrieval_mode(entities: WorkerEntities) -> str:
    """实体驱动的检索模式选择。

    - req_ids 非空 → precise（BM25 关键词精确匹配）
    - module 非空且无 req_ids → hybrid（BM25 + 向量等权融合）
    - 两者皆空 → semantic（纯向量语义检索）
    """
    req_ids = entities.get("req_ids", [])
    module = entities.get("module")

    if req_ids:
        return "precise"

    if module:
        return "hybrid"

    return "semantic"
