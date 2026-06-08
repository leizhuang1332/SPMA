"""Doc Agent 专属状态定义。"""

from typing import TypedDict, NotRequired


class DocAgentState(TypedDict, total=False):
    original_query: str
    entities: dict
    max_rounds: int
    timeout_ms: int

    round: int
    current_query: str
    weight_mode: str
    bm25_candidates: list[dict]
    vector_candidates: list[dict]
    fused_results: list[dict]
    accumulated_results: list[dict]

    assessment: str
    convergence_reason: str
    has_exact_match: bool
    hyde_enabled: bool

    final_results: list[dict]
    rounds_used: int
    total_latency_ms: int
