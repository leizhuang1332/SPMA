"""意图分类数据模型。

设计依据: SPMA-design-01 第五节 意图分类器
"""

from typing import Literal, TypedDict

SourceType = Literal["doc", "code", "sql"]
QueryType = Literal["trace", "search", "data_query", "explain"]


class ClassificationResult(TypedDict):
    """Supervisor 意图分类输出。"""

    sources: list[SourceType]
    is_cross_source: bool
    entities: dict | None
    query_type: QueryType


CLASSIFICATION_FAILURE_MODES = {
    "ambiguous_query": "模糊查询——默认三源全查",
    "term_ambiguity": "术语歧义——规则优先",
    "short_no_context": "无上下文短查询——反问或继承上一轮",
    "cross_source_miss": "跨源漏判——规则补刀",
}
