"""FP（假阳性）案例分析 —— 对"LLM说够了但实际不够"的案例分类和可修复性判断。"""

import json
from collections import Counter
from pathlib import Path
from typing import Any


FP_CATEGORIES = {
    "A": "实体遗漏型 — LLM 忽略了某个关键实体缺失",
    "B": "数量幻觉型 — LLM 被结果数量迷惑，不检查质量",
    "C": "语义理解错误 — LLM 误解了 query 或结果内容",
    "D": "跨源关联失败 — LLM 没发现跨源信息之间的缺口",
    "E": "其他 — 标注错误、极端 case 等",
}

REPAIRABLE = {"A", "B"}
STRUCTURAL = {"C", "D", "E"}


def analyze_fp_cases(
    judged_points: list[dict],
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """分析 FP 案例。

    Args:
        judged_points: 经 LLM 判断过的判断点列表（含 llm_verdict 和 golden_label）
        output_path: 可选的分析报告输出路径

    Returns:
        {
            "total_fp": int,
            "fp_rate": float,
            "category_distribution": {"A": 5, "B": 3, ...},
            "repairable_count": int,
            "structural_count": int,
            "repairable_ratio": float,
            "fp_cases": [...],
        }
    """
    fp_cases = []
    for p in judged_points:
        is_fp = (
            p["golden_label"] != "sufficient" and p.get("llm_verdict") == "sufficient"
        )
        if is_fp:
            fp_cases.append(p)

    total = len(judged_points)
    fp_count = len(fp_cases)
    fp_rate = fp_count / total if total > 0 else 0.0

    # 按 LLM 原始输出中的关键词做启发式分类
    categories = Counter()
    for case in fp_cases:
        raw = case.get("llm_raw_response", "").lower()
        if any(kw in raw for kw in ["实体", "entity", "missing entity"]):
            cat = "A"
        elif any(kw in raw for kw in ["数量", "count", "足够", "enough"]):
            cat = "B"
        elif any(kw in raw for kw in ["语义", "理解", "misunderstand"]):
            cat = "C"
        elif any(kw in raw for kw in ["跨源", "cross", "关联"]):
            cat = "D"
        else:
            cat = "E"
        categories[cat] += 1
        case["fp_category"] = cat

    repairable = sum(categories[c] for c in REPAIRABLE)
    structural = sum(categories[c] for c in STRUCTURAL)
    repairable_ratio = repairable / fp_count if fp_count > 0 else 0.0

    result = {
        "total_fp": fp_count,
        "fp_rate": round(fp_rate, 4),
        "category_distribution": dict(categories),
        "category_explanations": {k: FP_CATEGORIES[k] for k in categories},
        "repairable_count": repairable,
        "structural_count": structural,
        "repairable_ratio": round(repairable_ratio, 4),
        "fp_cases": [
            {
                "query_id": c["query_id"],
                "query_text": c.get("query_text", ""),
                "round": c.get("round", "?"),
                "category": c.get("fp_category", "E"),
                "llm_response_excerpt": c.get("llm_raw_response", "")[:300],
            }
            for c in fp_cases
        ],
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def print_fp_summary(analysis: dict) -> None:
    """打印 FP 分析摘要。"""
    print(f"\n{'='*50}")
    print("FP 案例分析摘要")
    print(f"{'='*50}")
    print(f"总 FP 数: {analysis['total_fp']}")
    print(f"FP 率: {analysis['fp_rate']:.2%}")
    print(f"\n分类分布:")
    for cat, count in analysis["category_distribution"].items():
        desc = FP_CATEGORIES.get(cat, "未知")
        print(f"  {cat} ({desc}): {count}")
    print(f"\n可修复 (A+B): {analysis['repairable_count']}")
    print(f"结构性 (C+D+E): {analysis['structural_count']}")
    print(f"可修复比例: {analysis['repairable_ratio']:.2%}")
    if analysis["repairable_ratio"] >= 0.6:
        print("✅ 大部分 FP 可通过 Prompt 改进修复")
    else:
        print("⚠️  超过40%的FP是结构性的 → Plan B 论据")
