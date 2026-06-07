"""跨模块共享的通用类型定义。"""

from typing import NotRequired, TypedDict


class RequestMetadata(TypedDict):
    """请求元数据——贯穿整个查询生命周期。"""

    request_id: str
    timestamp: str
    client_version: NotRequired[str]
    session_id: NotRequired[str]
    user_id: str


class AgentTrace(TypedDict, total=False):
    """一次查询中所有 Agent 的执行轨迹摘要。"""

    supervisor_rounds: int
    doc_rounds: int
    code_rounds: int
    sql_rounds: int
    synthesis_rounds: int
    total_llm_calls: int
    total_tokens: int
    estimated_cost_usd: float
    degradation_level: str
    convergence_reason: str


class DataFreshness(TypedDict, total=False):
    """各数据源的知识新鲜度时间戳。"""

    doc_updated_at: str
    code_indexed_at: str
    sql_schema_refreshed_at: str
