"""Doc Agent 专属状态定义。"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class BM25Hit(dict):
    """BM25 检索命中。doc_id, chunk_id, rank, score, snippet, metadata"""
    pass


class VectorHit(dict):
    """向量检索命中。doc_id, chunk_id, rank, score, snippet, metadata"""
    pass


class FusedResult(dict):
    """RRF 融合后结果。doc_id, chunk_id, rrf_score, bm25_rank, vector_rank, snippet, metadata"""
    pass


class DocAgentState(AgentState, total=False):
    """Doc Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities
    action: str
    bm25_candidates: list[BM25Hit]
    vector_candidates: list[VectorHit]
    fused_results: list[FusedResult]
    weight_mode: str
    assessment: str
    max_rounds: int
    timeout_ms: int
    token_budget: int
