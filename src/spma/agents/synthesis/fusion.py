"""Synthesis Agent 加权 RRF 融合——多 Worker citations 合并排序。"""

from spma.retrieval.rrf_fusion import weighted_fusion

DEFAULT_WORKER_WEIGHTS = {"prd": 1.0, "sql": 1.2, "code": 1.0}


def synthesize_fusion(worker_outputs: list[dict], weights: dict[str, float] | None = None, top_k: int = 20) -> list[dict]:
    if weights is None:
        weights = DEFAULT_WORKER_WEIGHTS

    source_groups: list[list[dict]] = []
    for output in worker_outputs:
        citations = output.get("citations", [])
        if not citations:
            continue
        for rank, citation in enumerate(citations):
            citation["worker_rank"] = rank
        source_groups.append(citations)

    if not source_groups:
        return []
    if len(source_groups) == 1:
        result = []
        for c in source_groups[0]:
            c["rrf_score"] = 1.0 / (1 + c.get("worker_rank", 0))
            result.append(c)
        return result[:top_k]

    return weighted_fusion(source_groups, weights=weights, top_k=top_k, k=60)
