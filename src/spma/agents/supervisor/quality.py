"""Supervisor 质量评分——三维(count+confidence+exact) x query_type权重矩阵。"""

from spma.models.worker_output import WorkerOutput

QUALITY_WEIGHTS = {
    "data_query": {"count": 0.3, "confidence": 0.3, "exact_match": 0.4},
    "search":     {"count": 0.4, "confidence": 0.4, "exact_match": 0.2},
    "trace":      {"count": 0.2, "confidence": 0.3, "exact_match": 0.5},
}


def score_worker(worker_output: WorkerOutput, query_type: str) -> float:
    weights = QUALITY_WEIGHTS.get(query_type, QUALITY_WEIGHTS["search"])
    result_count = worker_output.get("result_count", 0) or 0
    count_score = min(1.0, result_count / 3.0) * weights["count"]
    confidence = worker_output.get("confidence", 0) or 0
    confidence_score = confidence * weights["confidence"]
    has_exact = worker_output.get("has_exact_match", False)
    exact_score = (1.0 if has_exact else 0.0) * weights["exact_match"]
    return round(count_score + confidence_score + exact_score, 4)


def evaluate_workers(
    worker_outputs: list[WorkerOutput],
    query_type: str,
    threshold: float = 0.6,
) -> dict:
    scores: dict[str, float] = {}
    passed: list[str] = []
    failed: list[str] = []
    for output in worker_outputs:
        worker_type = output.get("worker_type", "unknown")
        score = score_worker(output, query_type)
        scores[worker_type] = score
        if score >= threshold:
            passed.append(worker_type)
        else:
            failed.append(worker_type)
    return {"scores": scores, "passed": passed, "failed": failed, "all_pass": len(failed) == 0 and len(passed) > 0}
