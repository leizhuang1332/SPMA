"""Synthesis Agent 专属状态定义。"""

from typing import TypedDict, NotRequired


class SynthesisAgentState(TypedDict, total=False):
    original_query: str
    worker_outputs: list
    max_rounds: int
    timeout_ms: int
    round: int

    fused_citations: list[dict]
    draft_answer: str

    audit_result: dict
    citation_coverage: float
    contradictions: list[dict]
    coverage_gaps: list[str]

    annotations: list[dict]

    final_answer: str
    convergence_reason: str
    total_latency_ms: int
