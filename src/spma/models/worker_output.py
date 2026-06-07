"""Worker Agent 输出契约。

设计依据: SPMA-design-07 第四节 Agent 交互协议
"""

from typing import Literal, NotRequired, TypedDict


class Citation(TypedDict):
    """引用元数据——每条检索结果的出处标注。"""

    source_type: Literal["prd", "code", "sql"]
    """来源数据源类型"""

    source_id: str
    """来源标识: doc_id:chunk_id, file_path:line, 或 table.column"""

    snippet: str
    """引用原文片段，≤200 字符"""

    relevance_score: NotRequired[float]
    """相关度分数 0-1"""

    metadata: NotRequired[dict]
    """额外元数据（版本、时间等）"""


# 使用函数式语法以支持 $schema 字段名（$ 在 class 语法中是非法标识符）
WorkerOutput = TypedDict("WorkerOutput", {
    "$schema": NotRequired[str],
    "task_id": NotRequired[str],
    "query_id": NotRequired[str],
    "worker_type": NotRequired[Literal["doc", "code", "sql"]],
    "result_count": NotRequired[int],
    "results": NotRequired[list[dict]],
    "citations": NotRequired[list[Citation]],
    "confidence": NotRequired[float],
    "has_exact_match": NotRequired[bool],
    "rounds_used": NotRequired[int],
    "convergence_reason": NotRequired[str],
    "total_llm_calls": NotRequired[int],
    "total_tokens": NotRequired[int],
    "latency_ms": NotRequired[int],
    "original_query": NotRequired[str],
    "degradation": NotRequired[dict],
    "discovered_entities": NotRequired[dict],
}, total=False)


# 使用函数式语法以支持 $schema 字段名
class WorkerDispatch(TypedDict, total=False):
    task_id: NotRequired[str]
    query_id: NotRequired[str]
    agent_type: NotRequired[Literal["doc", "code", "sql"]]
    original_query: NotRequired[str]
    rewritten_query: NotRequired[str]
    sub_queries: NotRequired[list[dict]]
    entities: NotRequired[dict]
    max_rounds: NotRequired[int]
    timeout_ms: NotRequired[int]
    token_budget: NotRequired[int]
    previous_results: NotRequired[list[dict]]
    hints_from_other_workers: NotRequired[dict]
    feature_flags: NotRequired[dict]
    model_override: NotRequired[str | None]


class DegradationInfo(TypedDict):
    """降级信息"""

    level: Literal["L0", "L1", "L2", "L3", "L4", "L5"]
    reason: str
    fallback_strategy: str
    impact_description: str


class DiscoveredEntities(TypedDict, total=False):
    """Worker 检索过程中发现的新实体——用于跨源桥接"""

    req_ids: list[str]
    table_names: list[str]
    code_refs: list[str]
    module: str | None
    person: str | None


# ============================================================
# SQL Agent 特有字段（Phase 1）
# ============================================================

SQLWorkerOutput = TypedDict("SQLWorkerOutput", {
    "$schema": NotRequired[str],
    "task_id": NotRequired[str],
    "query_id": NotRequired[str],
    "worker_type": NotRequired[Literal["doc", "code", "sql"]],
    "result_count": NotRequired[int],
    "results": NotRequired[list[dict]],
    "citations": NotRequired[list[Citation]],
    "confidence": NotRequired[float],
    "has_exact_match": NotRequired[bool],
    "rounds_used": NotRequired[int],
    "convergence_reason": NotRequired[str],
    "total_llm_calls": NotRequired[int],
    "total_tokens": NotRequired[int],
    "latency_ms": NotRequired[int],
    "original_query": NotRequired[str],
    "degradation": NotRequired[dict],
    "discovered_entities": NotRequired[dict],
    # SQL Agent 特有
    "execution_sql": NotRequired[str],
    "guard_risk_level": NotRequired[str],
    "quality_report": NotRequired[dict],
    "tables_used": NotRequired[list[str]],
    "columns_used": NotRequired[list[str]],
    "data_limitations": NotRequired[list[str]],
}, total=False)
