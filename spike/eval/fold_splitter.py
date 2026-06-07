"""5折交叉验证分割器。

按 query 级别分割（不是判断点级别），确保同一 query 的所有轮次在同一 fold 中。
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sklearn.model_selection import StratifiedKFold


class FoldSplitter:
    """将 golden 数据集按 query 做分层 5 折划分。"""

    def __init__(self, n_folds: int = 5, random_seed: int = 42) -> None:
        self.n_folds = n_folds
        self.random_seed = random_seed

    def split(self, golden_dataset_path: str | Path) -> list[dict[str, Any]]:
        """读取数据集并按 query 分层划分。

        Args:
            golden_dataset_path: golden_eval_dataset.json 路径

        Returns:
            [
              {
                "fold": 0,
                "train": [judgment_points...],
                "test": [judgment_points...],
              },
              ...
            ]
        """
        with open(golden_dataset_path, encoding="utf-8") as f:
            points = json.load(f)

        # 按 query_id 分组
        query_groups = defaultdict(list)
        for point in points:
            query_groups[point["query_id"]].append(point)

        query_ids = sorted(query_groups.keys())

        # 每 query 取第一条判断点的 label 作为分层标签
        y = [
            query_groups[qid][0]["golden_label"]
            for qid in query_ids
        ]

        skf = StratifiedKFold(
            n_splits=self.n_folds, shuffle=True, random_state=self.random_seed
        )

        folds = []
        for fold_idx, (train_qids_idx, test_qids_idx) in enumerate(skf.split(query_ids, y)):
            train_qids = {query_ids[i] for i in train_qids_idx}
            test_qids = {query_ids[i] for i in test_qids_idx}

            train_points = [p for p in points if p["query_id"] in train_qids]
            test_points = [p for p in points if p["query_id"] in test_qids]

            folds.append(
                {
                    "fold": fold_idx,
                    "train": train_points,
                    "test": test_points,
                    "train_query_count": len(train_qids),
                    "test_query_count": len(test_qids),
                }
            )

        return folds


def save_folds(folds: list[dict], output_path: str | Path) -> None:
    """保存折划分结果到 JSON 文件（用于可复现评估）。"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(folds, f, ensure_ascii=False, indent=2)
