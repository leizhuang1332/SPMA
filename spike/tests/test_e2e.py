"""端到端集成测试 —— 用最小 mock 数据验证管线可跑。"""

import json
import tempfile
from pathlib import Path

import pytest

from spike.eval.convergence_eval import validate_data
from spike.eval.fold_splitter import FoldSplitter
from spike.eval.metrics import cohens_kappa, compute_metrics


# 最小 mock 数据
MOCK_JUDGMENT_POINTS = [
    {
        "query_id": "q001",
        "query_text": "测试问题1",
        "query_type": "single_doc",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_001:chunk_0", "content": "测试内容"}],
        "golden_label": "sufficient",
        "llm_verdict": "sufficient",
        "llm_confidence": 0.9,
        "llm_raw_response": "够了",
    },
    {
        "query_id": "q001",
        "query_text": "测试问题1",
        "query_type": "single_doc",
        "source_type": "doc",
        "round": 2,
        "results": [{"id": "doc_001:chunk_0", "content": "测试内容"}, {"id": "doc_001:chunk_1", "content": "更多"}],
        "golden_label": "sufficient",
        "llm_verdict": "sufficient",
        "llm_confidence": 0.95,
        "llm_raw_response": "够了",
    },
    {
        "query_id": "q002",
        "query_text": "测试问题2",
        "query_type": "cross_source",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_002:chunk_0", "content": "部分内容"}],
        "golden_label": "insufficient",
        "llm_verdict": "insufficient",
        "llm_confidence": 0.8,
        "llm_raw_response": "不够",
    },
    {
        "query_id": "q002",
        "query_text": "测试问题2",
        "query_type": "cross_source",
        "source_type": "doc",
        "round": 2,
        "results": [
            {"id": "doc_002:chunk_0", "content": "部分内容"},
            {"id": "doc_002:chunk_1", "content": "补充内容"},
        ],
        "golden_label": "insufficient",
        "llm_verdict": "sufficient",  # FP!
        "llm_confidence": 0.6,
        "llm_raw_response": "关键实体覆盖完整，数量充足",
    },
    {
        "query_id": "q003",
        "query_text": "测试问题3",
        "query_type": "single_doc",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_003:chunk_0", "content": "内容三"}],
        "golden_label": "sufficient",
        "llm_verdict": "sufficient",
        "llm_confidence": 0.85,
        "llm_raw_response": "足够",
    },
    {
        "query_id": "q004",
        "query_text": "测试问题4",
        "query_type": "cross_source",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_004:chunk_0", "content": "内容四"}],
        "golden_label": "insufficient",
        "llm_verdict": "insufficient",
        "llm_confidence": 0.75,
        "llm_raw_response": "不够",
    },
]


class TestMetrics:
    """评估指标单元测试。"""

    def test_perfect_precision(self):
        y_true = [True, True, False, False]
        y_pred = [True, True, False, False]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0
        assert metrics["tp"] == 2
        assert metrics["fp"] == 0

    def test_one_false_positive(self):
        y_true = [True, False, False]
        y_pred = [True, True, False]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 0.5  # 1 TP, 1 FP
        assert metrics["fp"] == 1

    def test_zero_division(self):
        y_true = [False, False]
        y_pred = [True, True]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0


class TestKappa:
    """Cohen's Kappa 测试。"""

    def test_perfect_agreement(self):
        y_a = [True, True, False, False]
        y_b = [True, True, False, False]
        kappa = cohens_kappa(y_a, y_b)
        assert kappa == 1.0

    def test_chance_agreement(self):
        # 50% positive, random agreement
        y_a = [True, False, True, False]
        y_b = [True, False, False, True]
        kappa = cohens_kappa(y_a, y_b)
        assert -1.0 <= kappa <= 1.0

    def test_complete_disagreement(self):
        y_a = [True, True]
        y_b = [False, False]
        kappa = cohens_kappa(y_a, y_b)
        assert kappa <= 0.0


class TestFoldSplitter:
    """5折 CV 分割器测试。"""

    def test_query_level_split(self):
        """验证同一 query 的所有判断点在同一 fold 中。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(MOCK_JUDGMENT_POINTS, f)
            tmp_path = f.name

        try:
            splitter = FoldSplitter(n_folds=2, random_seed=42)
            folds = splitter.split(tmp_path)

            assert len(folds) == 2

            for fold in folds:
                # 每个 test set 中的 query_id 不应出现在 train set 中
                test_qids = {p["query_id"] for p in fold["test"]}
                train_qids = {p["query_id"] for p in fold["train"]}
                assert test_qids.isdisjoint(train_qids), "test 和 train 的 query 有重叠"
        finally:
            Path(tmp_path).unlink()


class TestValidateData:
    """数据验证测试。"""

    def test_valid_data(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(MOCK_JUDGMENT_POINTS, f)
            tmp_path = f.name

        try:
            assert validate_data(tmp_path) is True
        finally:
            Path(tmp_path).unlink()

    def test_missing_field(self):
        bad_data = [{"query_id": "q001"}]  # 缺少多个字段
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad_data, f)
            tmp_path = f.name

        try:
            assert validate_data(tmp_path) is False
        finally:
            Path(tmp_path).unlink()
