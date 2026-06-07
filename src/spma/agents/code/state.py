"""Code Agent 专属状态定义。"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class SearchTermSet(dict):
    """搜索词集合。exact_terms, fuzzy_terms, tag_terms"""
    pass


class RipgrepHit(dict):
    """ripgrep 搜索结果。repo, file_path, line_number, match_text, match_type, confidence"""
    pass


class ExpandedFile(dict):
    """AST 调用图扩展结果。repo, file_path, file_content, imports, calls, called_by, relation_to_seed, depth"""
    pass


class CodeAgentState(AgentState, total=False):
    """Code Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities
    search_terms: SearchTermSet
    candidate_repos: list[str]
    route_method: str
    route_confidence: str
    ripgrep_results: list[RipgrepHit]
    expanded_context: list[ExpandedFile]
    assessment: str
    call_depth: int
    new_files_this_round: int
    fallback_layer: int
    fallback_method: str
    max_rounds: int
    timeout_ms: int
    token_budget: int
