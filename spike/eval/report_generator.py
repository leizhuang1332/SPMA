"""分层分析 & 评估报告生成。"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from spike.eval.metrics import compute_metrics


def stratified_analysis(judged_points: list[dict]) -> dict[str, Any]:
    """按多个维度做分层分析。

    Returns:
        {
            "by_query_type": {"single_doc": {metrics}, ...},
            "by_round": {"1": {metrics}, ...},
            "by_query_length": {"short": {metrics}, ...},
            "by_result_count": {"few": {metrics}, ...},
        }
    """
    return {
        "by_query_type": _analyze_by(judged_points, _get_query_type),
        "by_round": _analyze_by(judged_points, lambda p: str(p.get("round", "?"))),
        "by_query_length": _analyze_by(judged_points, _get_query_length),
        "by_result_count": _analyze_by(judged_points, _get_result_count),
    }


def _analyze_by(
    points: list[dict], key_fn: callable
) -> dict[str, dict]:
    """按 key_fn 分组计算指标。"""
    groups = defaultdict(list)
    for p in points:
        key = key_fn(p)
        groups[key].append(p)

    result = {}
    for key, group in sorted(groups.items()):
        y_true = [p["golden_label"] == "sufficient" for p in group]
        y_pred = [p.get("llm_verdict") == "sufficient" for p in group]
        metrics = compute_metrics(y_true, y_pred)
        result[key] = {**metrics, "sample_count": len(group)}
    return result


def _get_query_type(point: dict) -> str:
    return point.get("query_type", "unknown")


def _get_query_length(point: dict) -> str:
    text = point.get("query_text", "")
    length = len(text)
    if length < 15:
        return "short"
    elif length <= 50:
        return "medium"
    else:
        return "long"


def _get_result_count(point: dict) -> str:
    count = len(point.get("results", []))
    if count <= 2:
        return "1-2"
    elif count <= 5:
        return "3-5"
    elif count <= 10:
        return "6-10"
    else:
        return ">10"


def generate_report(
    fold_metrics: list[dict],
    stratified: dict,
    fp_analysis: dict,
    prompt_version: str,
    output_path: str | Path,
) -> None:
    """生成 Markdown 评估报告。"""
    from spike.eval.metrics import compute_fold_metrics

    aggregated = compute_fold_metrics(fold_metrics)

    lines = [
        f"# Phase 0 收敛判断 Spike — 评估报告",
        "",
        f"> 生成时间: 自动生成 | Prompt 版本: {prompt_version}",
        "",
        "---",
        "",
        "## 1. 总体指标",
        "",
        "| 指标 | 均值 | 标准差 |",
        "|------|------|--------|",
        f"| 精确率 | {aggregated['precision']['mean']:.4f} | ±{aggregated['precision']['std']:.4f} |",
        f"| 召回率 | {aggregated['recall']['mean']:.4f} | ±{aggregated['recall']['std']:.4f} |",
        f"| F1 | {aggregated['f1']['mean']:.4f} | ±{aggregated['f1']['std']:.4f} |",
        f"| 特异度 | {aggregated['specificity']['mean']:.4f} | ±{aggregated['specificity']['std']:.4f} |",
        f"| 判断点总数 | {aggregated['total_judgment_points']} | — |",
        "",
        "### Go/No-Go 判定",
        "",
    ]

    precision = aggregated["precision"]["mean"]
    if precision >= 0.85:
        lines.append(f"✅ **Go** — 精确率 {precision:.2%} ≥ 85%，强烈信心进入 Phase 1")
    elif precision >= 0.80:
        lines.append(f"✅ **Go (有条件)** — 精确率 {precision:.2%} ∈ [80%, 85%)，Agent 循环中增加确定性收敛权重")
    elif precision >= 0.70:
        lines.append(f"⚠️ **Risk** — 精确率 {precision:.2%} ∈ [70%, 80%)，需评审 FP 案例可修复性")
    else:
        lines.append(f"❌ **No-Go** — 精确率 {precision:.2%} < 70%，启用 Plan B")

    lines.extend([
        "",
        "---",
        "",
        "## 2. 各折详情",
        "",
        "| Fold | 精确率 | 召回率 | F1 | 判断点数 |",
        "|------|--------|--------|----|---------|",
    ])

    for i, fm in enumerate(fold_metrics):
        lines.append(
            f"| {i+1} | {fm['precision']:.4f} | {fm['recall']:.4f} | {fm['f1']:.4f} | {fm['total']} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 3. 分层分析",
        "",
    ])

    for dim_name, dim_data in stratified.items():
        lines.append(f"### {dim_name}")
        lines.append("")
        lines.append("| 分组 | 精确率 | 召回率 | F1 | 样本数 |")
        lines.append("|------|--------|--------|----|--------|")
        for key, metrics in dim_data.items():
            lines.append(
                f"| {key} | {metrics['precision']:.4f} | {metrics['recall']:.4f} | {metrics['f1']:.4f} | {metrics['sample_count']} |"
            )
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 4. FP 案例分析",
        "",
        f"- 总 FP 数: {fp_analysis['total_fp']}",
        f"- FP 率: {fp_analysis['fp_rate']:.2%}",
        f"- 可修复比例: {fp_analysis['repairable_ratio']:.2%}",
        "",
        "### FP 分类分布",
        "",
        "| 类别 | 描述 | 数量 |",
        "|------|------|------|",
    ])

    for cat, count in fp_analysis.get("category_distribution", {}).items():
        desc = {
            "A": "实体遗漏型", "B": "数量幻觉型",
            "C": "语义理解错误", "D": "跨源关联失败", "E": "其他",
        }.get(cat, "未知")
        lines.append(f"| {cat} | {desc} | {count} |")

    lines.extend([
        "",
        "### 是否可通过 Prompt 修复？",
        "",
        f"可修复率 {fp_analysis['repairable_ratio']:.2%} → "
        f"{'建议改进Prompt后重新评估' if fp_analysis['repairable_ratio'] >= 0.6 else '存在结构性缺陷，需考虑Plan B'}",
    ])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
