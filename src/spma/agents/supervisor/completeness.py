"""实体完备度评估——确定性代码（不调用 LLM）。

设计依据: SPMA-design-01 第七节 实体完备度评估
"""

from spma.models.entities import CompletenessLevel, ExtractedEntities


def evaluate_completeness(entities: ExtractedEntities) -> tuple[str, int, str]:
    """评估抽取实体的完备度——纯 Python 函数，不调 LLM。

    信息增益加权打分:
    - req_ids/table_names/code_refs → 10 分（精确匹配级）
    - module → 5 分（语义锚点级）
    - time_range/person/version → 2 分（辅助过滤级）
    - group_by/doc_types → 1 分（轻度过滤级）

    Returns: (level: "rich"|"partial"|"bare", score: int, strategy: str)
    """
    raise NotImplementedError


def route_by_completeness(level: str, user_query: str, context: dict) -> dict:
    """根据完备度等级选择处置策略。

    Returns: 调度指令 {strategy, query, sources, note}
    """
    raise NotImplementedError
