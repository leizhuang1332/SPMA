# src/spma/retrieval/rrf_fusion.py
"""RRF (Reciprocal Rank Fusion) 融合算法——等权 + 加权。

等权 RRF: score(chunk) = sum(1 / (k + rank_i))  for each source i
加权 RRF: score(chunk) = sum(w_i / (k + rank_i))  for each source i

k=60 为标准选择（学界和工业界验证的最稳健常数）。
"""


def equal_weight_fusion(
    source_a: list[dict],
    source_b: list[dict],
    top_k: int = 10,
    k: int = 60,
) -> list[dict]:
    """等权 RRF 融合两个来源的检索结果。

    Args:
        source_a: 第一个来源的结果列表，每项含 chunk_id 和 score
        source_b: 第二个来源的结果列表
        top_k: 返回数量
        k: RRF 常数

    Returns:
        按 rrf_score 降序的融合结果列表
    """
    rrf_scores: dict[str, float] = {}
    best_meta: dict[str, dict] = {}

    for rank, item in enumerate(source_a):
        cid = item["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k + rank)
        if cid not in best_meta:
            best_meta[cid] = dict(item)

    for rank, item in enumerate(source_b):
        cid = item["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k + rank)
        if cid not in best_meta:
            best_meta[cid] = dict(item)

    sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, rrf_score in sorted_chunks[:top_k]:
        entry = {"chunk_id": cid, "rrf_score": rrf_score, **best_meta[cid]}
        results.append(entry)

    return results


def weighted_fusion(
    source_groups: list[list[dict]],
    weights: dict[str, float],
    top_k: int = 10,
    k: int = 60,
) -> list[dict]:
    """加权 RRF 融合多个 Worker 来源的结果。

    Args:
        source_groups: 每个 Worker 的结果列表，每项含 source_type 和 worker_rank
        weights: {source_type: weight} 映射
        top_k: 返回数量
        k: RRF 常数

    Returns:
        按加权 rrf_score 降序的融合结果列表
    """
    rrf_scores: dict[str, float] = {}
    best_meta: dict[str, dict] = {}

    for group in source_groups:
        if not group:
            continue
        source_type = group[0].get("source_type", "unknown")
        w = weights.get(source_type, 1.0)

        for item in group:
            rank = item.get("worker_rank", 0)
            cid = item["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + w / (k + rank)
            if cid not in best_meta:
                best_meta[cid] = dict(item)

    sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, rrf_score in sorted_chunks[:top_k]:
        entry = {"chunk_id": cid, "rrf_score": rrf_score, **best_meta[cid]}
        results.append(entry)

    return results
