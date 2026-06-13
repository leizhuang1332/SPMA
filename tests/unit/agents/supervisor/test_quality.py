import pytest
from spma.agents.supervisor.quality import score_worker, evaluate_workers


class TestQualityScoring:
    def test_score_worker_with_results_and_exact_match(self):
        output = {"worker_type": "code", "result_count": 5, "confidence": 0.9, "has_exact_match": True}
        score = score_worker(output, "search")
        # count: min(1,5/3)*0.4=0.4, confidence: 0.9*0.4=0.36, exact: 1*0.2=0.2 => 0.96
        assert score > 0.9

    def test_score_worker_empty_results(self):
        output = {"worker_type": "doc", "result_count": 0, "confidence": 0, "has_exact_match": False}
        score = score_worker(output, "search")
        assert score == 0.0

    def test_evaluate_workers_all_pass(self):
        outputs = [
            {"worker_type": "doc", "result_count": 5, "confidence": 0.9, "has_exact_match": True},
            {"worker_type": "code", "result_count": 4, "confidence": 0.85, "has_exact_match": True},
        ]
        result = evaluate_workers(outputs, "search", threshold=0.6)
        assert result["all_pass"] is True
        assert len(result["failed"]) == 0

    def test_evaluate_workers_with_failure(self):
        outputs = [
            {"worker_type": "doc", "result_count": 5, "confidence": 0.9, "has_exact_match": True},
            {"worker_type": "code", "result_count": 0, "confidence": 0, "has_exact_match": False},
        ]
        result = evaluate_workers(outputs, "search", threshold=0.6)
        assert result["all_pass"] is False
        assert "code" in result["failed"]

    def test_diff_query_type_weights(self):
        output = {"worker_type": "sql", "result_count": 3, "confidence": 0.8, "has_exact_match": False}
        search_score = score_worker(output, "search")
        trace_score = score_worker(output, "trace")
        # trace exact_match weight (0.5) is higher than search (0.2), but exact is false, so trace should be lower overall
        assert search_score != trace_score
