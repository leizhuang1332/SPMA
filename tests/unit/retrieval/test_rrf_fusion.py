# tests/unit/retrieval/test_rrf_fusion.py
import pytest
from spma.retrieval.rrf_fusion import equal_weight_fusion, weighted_fusion


class TestEqualWeightFusion:
    def test_basic_fusion_sorts_by_rrf_score(self):
        """两个来源各 3 条结果，验证 RRF 融合排序正确。"""
        bm25_results = [
            {"chunk_id": "a", "score": 0.9},
            {"chunk_id": "b", "score": 0.7},
            {"chunk_id": "c", "score": 0.5},
        ]
        vector_results = [
            {"chunk_id": "b", "score": 0.8},
            {"chunk_id": "c", "score": 0.6},
            {"chunk_id": "d", "score": 0.4},
        ]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=4, k=60)

        # chunk "b" 在两个来源都排高位 → 应该第一
        assert fused[0]["chunk_id"] == "b"
        # 所有 chunk 排名不超出输入范围
        chunk_ids = [r["chunk_id"] for r in fused]
        assert set(chunk_ids) <= {"a", "b", "c", "d"}
        # RRF 分数降序
        scores = [r["rrf_score"] for r in fused]
        assert scores == sorted(scores, reverse=True)

    def test_fusion_respects_top_k(self):
        """验证 top_k 截断。"""
        bm25_results = [{"chunk_id": f"bm{i}", "score": 1.0 - i * 0.1} for i in range(10)]
        vector_results = [{"chunk_id": f"vec{i}", "score": 1.0 - i * 0.1} for i in range(10)]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=5, k=60)

        assert len(fused) == 5

    def test_fusion_deduplicates_by_chunk_id(self):
        """同一 chunk 出现在两边 → 合并为一条，取最佳排名。"""
        bm25_results = [
            {"chunk_id": "shared", "score": 0.9, "snippet": "bm25"},
        ]
        vector_results = [
            {"chunk_id": "shared", "score": 0.8, "snippet": "vector"},
        ]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=10, k=60)

        assert len(fused) == 1
        assert fused[0]["chunk_id"] == "shared"

    def test_empty_inputs(self):
        """空输入返回空列表。"""
        assert equal_weight_fusion([], [], top_k=10, k=60) == []
        result = equal_weight_fusion([{"chunk_id": "a", "score": 0.9}], [], top_k=10, k=60)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "a"


class TestWeightedFusion:
    def test_weighted_fusion_respects_weights(self):
        """SQL 权重 1.2 > Doc 权重 1.0 — SQL 结果排序提升。"""
        doc_results = [
            {"chunk_id": "x", "score": 0.9, "source_type": "doc", "worker_rank": 0},
            {"chunk_id": "y", "score": 0.7, "source_type": "doc", "worker_rank": 1},
        ]
        sql_results = [
            {"chunk_id": "z", "score": 0.6, "source_type": "sql", "worker_rank": 0},
        ]
        weights = {"doc": 1.0, "sql": 1.2}

        fused = weighted_fusion([doc_results, sql_results], weights=weights, top_k=10, k=60)

        # SQL 的 "z" 虽然分数低，但权重 1.2 拉高了 RRF 分
        assert len(fused) == 3
        # z (weight 1.2, rank 0) should outrank y (weight 1.0, rank 1)
        assert fused[0]["chunk_id"] == "z", "z with higher weight should rank first"
        z_rrf = next(r["rrf_score"] for r in fused if r["chunk_id"] == "z")
        y_rrf = next(r["rrf_score"] for r in fused if r["chunk_id"] == "y")
        assert z_rrf > y_rrf, (
            f"z ({z_rrf}) with weight 1.2 should outrank y ({y_rrf}) with weight 1.0"
        )
