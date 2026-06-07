"""实体抽取与分发的数据模型。

设计依据: SPMA-design-01 第六节 实体抽取
"""

from typing import TypedDict, NotRequired


class ExtractedEntities(TypedDict, total=False):
    """Supervisor 从用户问题中抽取的结构化实体。"""

    module: str | None
    req_ids: list[str]
    time_range: str | None
    version: str | None
    table_names: list[str]
    column_names: list[str]
    metrics: list[str]
    group_by: str | None
    code_refs: list[str]
    person: str | None
    doc_types: list[str]


class WorkerEntities(TypedDict, total=False):
    """Supervisor 抽取的实体子集——下发给单个 Worker 的视角。"""

    module: str | None
    req_ids: list[str]
    time_range: str | None
    version: str | None
    table_names: list[str]
    column_names: list[str]
    metrics: list[str]
    group_by: str | None
    code_refs: list[str]
    person: str | None
    doc_types: list[str]


class CompletenessLevel:
    """实体完备度等级（确定性代码评估，非 LLM）。"""

    WEIGHT_EXACT_MATCH = 10
    WEIGHT_SEMANTIC_ANCHOR = 5
    WEIGHT_AUXILIARY = 2
    WEIGHT_LIGHT = 1
    RICH = 10
    PARTIAL = 5
    BARE = 0
