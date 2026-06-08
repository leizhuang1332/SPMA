"""检索日志数据结构。"""

from typing import TypedDict, NotRequired


class SearchLogEntry(TypedDict, total=False):
    log_id: str
    timestamp: str
    worker_type: str
    worker_version: str
    query_id: str
    query_text: str
    query_type: str
    trigger: str
    entities: dict
    agent_rounds: int
    convergence_reason: str
    bm25_candidates: list[dict]
    vector_candidates: list[dict]
    rrf_fused: list[dict]
    latency_ms: int
    feedback: dict | None
