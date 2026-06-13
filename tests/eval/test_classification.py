"""分类准确率评估——基于 classification_eval.json 数据集。"""

import json
import pytest
from pathlib import Path


@pytest.mark.anyio
class TestClassificationEval:
    async def test_classification_accuracy(self):
        eval_path = Path(__file__).parent.parent.parent / "data" / "classification_eval.json"
        if not eval_path.exists():
            pytest.skip("评估数据不存在")
        with open(eval_path) as f:
            data = json.load(f)

        from spma.agents.supervisor.classifier_rules import apply_rules
        from spma.models.classification import ClassificationResult

        correct = 0
        for item in data:
            result = apply_rules(item["query"], ClassificationResult(
                sources=[], is_cross_source=False, query_type="search", entities={}))
            golden = set(item["golden_sources"])
            predicted = set(result["sources"])
            if golden == predicted:
                correct += 1

        accuracy = correct / len(data) if data else 0
        print(f"\n分类准确率: {accuracy:.2%} ({correct}/{len(data)})")
        assert accuracy >= 0.80
