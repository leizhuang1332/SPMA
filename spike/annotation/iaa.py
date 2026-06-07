"""标注一致率 (IAA) 计算 —— Cohen's Kappa。"""

import json
from pathlib import Path

from spike.eval.metrics import cohens_kappa


def compute_iaa(annotations_path: str | Path) -> dict:
    """计算双人标注的 Cohen's Kappa。

    Args:
        annotations_path: 标注文件路径 (JSON)
            格式: [
              {
                "query_id": "q001",
                "round": 1,
                "annotator_a": {"label": "S2", "sufficient": true},
                "annotator_b": {"label": "S1", "sufficient": true},
              },
              ...
            ]

    Returns:
        {
            "kappa": float,
            "total_points": int,
            "agreements": int,
            "disagreements": int,
            "agreement_rate": float,
            "disagreement_cases": [...],
        }
    """
    with open(annotations_path, encoding="utf-8") as f:
        annotations = json.load(f)

    y_a = []
    y_b = []
    agreements = 0
    disagreements = 0
    disagreement_cases = []

    for ann in annotations:
        suff_a = ann["annotator_a"]["sufficient"]
        suff_b = ann["annotator_b"]["sufficient"]
        y_a.append(suff_a)
        y_b.append(suff_b)

        if suff_a == suff_b:
            agreements += 1
        else:
            disagreements += 1
            disagreement_cases.append(
                {
                    "query_id": ann["query_id"],
                    "round": ann["round"],
                    "annotator_a": ann["annotator_a"]["label"],
                    "annotator_b": ann["annotator_b"]["label"],
                }
            )

    kappa = cohens_kappa(y_a, y_b)
    total = len(annotations)
    agreement_rate = agreements / total if total > 0 else 0.0

    return {
        "kappa": kappa,
        "total_points": total,
        "agreements": agreements,
        "disagreements": disagreements,
        "agreement_rate": round(agreement_rate, 4),
        "disagreement_cases": disagreement_cases,
    }


def print_iaa_report(result: dict) -> None:
    """打印 IAA 报告。"""
    print(f"\n{'='*50}")
    print("标注一致率 (IAA) 报告")
    print(f"{'='*50}")
    print(f"Cohen's Kappa: {result['kappa']:.4f}")
    print(f"一致率: {result['agreement_rate']:.2%}")
    print(f"一致: {result['agreements']} | 分歧: {result['disagreements']} | 总计: {result['total_points']}")

    if result["kappa"] >= 0.8:
        print("✅ IAA 达标 (κ ≥ 0.8)")
    elif result["kappa"] >= 0.6:
        print("⚠️  IAA 可接受但需改进 Rubric (0.6 ≤ κ < 0.8)")
    else:
        print("❌ IAA 不达标 (κ < 0.6)，需重新设计 Rubric")

    if result["disagreement_cases"]:
        print(f"\n分歧案例 (前 5 条):")
        for case in result["disagreement_cases"][:5]:
            print(f"  - {case['query_id']} R{case['round']}: A={case['annotator_a']}, B={case['annotator_b']}")
