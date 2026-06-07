"""Synthesis Agent 专属状态定义。"""

from spma.models.agent_state import AgentState


class FusedCitation(dict):
    """RRF 融合后的引用: source_type, source_id, snippet, rrf_score, worker_confidence, source_rankings"""
    pass


class UnverifiedCitation(dict):
    """无法验证的引用: source_id, reason, impact"""
    pass


class CrossSourceContradiction(dict):
    """跨源矛盾: claim, source_a, source_a_claim, source_b, source_b_claim, resolution"""
    pass


class SynthesisAgentState(AgentState, total=False):
    """Synthesis Agent 专属状态字段。"""

    original_query: str
    worker_outputs: list
    draft_answer: str
    fused_citations: list[FusedCitation]
    rrf_params: dict
    citation_coverage: float
    unverified_citations: list[UnverifiedCitation]
    contradictions: list[CrossSourceContradiction]
    coverage_gaps: list[str]
    final_answer: str
    final_citations: list[dict]
    audit_trail: str
    max_rounds: int
    timeout_ms: int
    token_budget: int
