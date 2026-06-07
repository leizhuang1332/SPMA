"""主评估脚本 —— 跑 5 折 CV 评估 LLM 完备度判断精确率。

用法:
    # 消融实验: 对所有 Prompt 版本跑5折CV
    python -m spike.eval.convergence_eval --mode ablation

    # 最终评估: 只跑 final prompt
    python -m spike.eval.convergence_eval --mode final --prompt-version final

    # 验证数据完整性
    python -m spike.eval.convergence_eval --validate-data
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from spike.eval.fold_splitter import FoldSplitter, save_folds
from spike.eval.fp_analyzer import analyze_fp_cases, print_fp_summary
from spike.eval.llm_judge import LLMJudge
from spike.eval.metrics import ClassificationMetrics, compute_fold_metrics, compute_metrics
from spike.eval.report_generator import generate_report, stratified_analysis


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_data(golden_path: str) -> bool:
    """验证 golden 数据集的完整性。"""
    with open(golden_path, encoding="utf-8") as f:
        points = json.load(f)

    required_fields = [
        "query_id", "query_text", "query_type", "source_type",
        "round", "results", "golden_label",
    ]
    errors = []

    for i, point in enumerate(points):
        for field in required_fields:
            if field not in point:
                errors.append(f"判断点 #{i}: 缺少字段 '{field}'")

        label = point.get("golden_label")
        if label not in ("sufficient", "insufficient"):
            errors.append(f"判断点 #{i}: golden_label 值无效 '{label}'")

        if not isinstance(point.get("results"), list):
            errors.append(f"判断点 #{i}: results 必须是列表")

    if errors:
        print("❌ 数据验证失败:")
        for err in errors[:20]:
            print(f"  - {err}")
        if len(errors) > 20:
            print(f"  ... 及 {len(errors) - 20} 个其他错误")
        return False

    query_ids = set(p["query_id"] for p in points)
    print(f"✅ 数据验证通过: {len(points)} 个判断点, {len(query_ids)} 个 query")
    return True


def run_fold_evaluation(
    fold: dict, prompt_version: str, judge: LLMJudge
) -> ClassificationMetrics:
    """在单个 fold 的 test 集上跑评估。

    Args:
        fold: 5折划分中的一折
        prompt_version: Prompt 版本
        judge: LLM 判断器

    Returns:
        ClassificationMetrics
    """
    test_points = fold["test"]
    judged = judge.judge_batch(test_points, prompt_version)

    y_true = [p["golden_label"] == "sufficient" for p in judged]
    y_pred = [p["llm_verdict"] == "sufficient" for p in judged]

    metrics = compute_metrics(y_true, y_pred)

    # 附加 FP 详情
    fp_cases = []
    for p in judged:
        t = p["golden_label"] == "sufficient"
        p_hat = p["llm_verdict"] == "sufficient"
        if not t and p_hat:
            fp_cases.append(
                {
                    "query_id": p["query_id"],
                    "query_text": p["query_text"],
                    "round": p["round"],
                    "llm_raw_response": p.get("llm_raw_response", ""),
                }
            )

    return {
        **metrics,
        "fp_cases": fp_cases,
    }


def run_ablation(config: dict, prompt_versions: list[str]) -> dict:
    """消融实验：对所有 Prompt 版本跑 5 折 CV。"""
    golden_path = config["paths"]["golden_dataset"]
    n_folds = config["evaluation"]["folds"]
    seed = config["evaluation"]["random_seed"]

    splitter = FoldSplitter(n_folds=n_folds, random_seed=seed)
    folds = splitter.split(golden_path)
    save_folds(folds, "data/folds.json")

    judge = LLMJudge()

    all_results = {}
    for version in prompt_versions:
        print(f"\n{'='*60}")
        print(f"评估 Prompt {version}...")
        print(f"{'='*60}")

        fold_metrics = []
        for fold in folds:
            print(f"  Fold {fold['fold'] + 1}/{n_folds}...")
            metrics = run_fold_evaluation(fold, version, judge)
            fold_metrics.append(metrics)

        aggregated = compute_fold_metrics(fold_metrics)
        all_results[version] = {
            "fold_metrics": fold_metrics,
            "aggregated": aggregated,
        }

    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description="收敛判断 Spike 评估")
    parser.add_argument(
        "--mode",
        choices=["ablation", "final"],
        default="ablation",
        help="评估模式",
    )
    parser.add_argument(
        "--prompt-version",
        choices=["A", "B", "C", "final"],
        default="B",
        help="Prompt 版本（final 模式使用）",
    )
    parser.add_argument(
        "--validate-data",
        action="store_true",
        help="验证 golden 数据集完整性",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.validate_data:
        valid = validate_data(config["paths"]["golden_dataset"])
        sys.exit(0 if valid else 1)

    if args.mode == "ablation":
        results = run_ablation(config, ["A", "B", "C"])
        output_path = str(Path(config["paths"]["prompts_dir"]) / "ablation_results.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n消融结果已保存: {output_path}")

    elif args.mode == "final":
        splitter = FoldSplitter(
            n_folds=config["evaluation"]["folds"],
            random_seed=config["evaluation"]["random_seed"],
        )
        folds = splitter.split(config["paths"]["golden_dataset"])
        judge = LLMJudge()

        fold_metrics = []
        for fold in folds:
            print(f"Fold {fold['fold'] + 1}...")
            metrics = run_fold_evaluation(fold, args.prompt_version, judge)
            fold_metrics.append(metrics)

        aggregated = compute_fold_metrics(fold_metrics)
        print(f"\n最终评估结果:")
        print(f"  精确率: {aggregated['precision']['mean']:.4f} ± {aggregated['precision']['std']:.4f}")
        print(f"  召回率: {aggregated['recall']['mean']:.4f} ± {aggregated['recall']['std']:.4f}")

        # 收集所有 fold 的已判断点用于分层分析和 FP 分析
        all_judged = []
        for fold in folds:
            test_points = fold["test"]
            judged = judge.judge_batch(test_points, args.prompt_version)
            all_judged.extend(judged)

        # FP 分析
        reports_dir = Path(config["paths"]["reports_dir"])
        reports_dir.mkdir(parents=True, exist_ok=True)
        fp_analysis = analyze_fp_cases(all_judged, output_path=str(reports_dir / "fp_analysis.json"))
        print_fp_summary(fp_analysis)

        # 分层分析 + 报告生成
        stratified = stratified_analysis(all_judged)
        report_path = reports_dir / "evaluation_report.md"
        generate_report(fold_metrics, stratified, fp_analysis, args.prompt_version, report_path)

        print(f"\n评估报告已保存: {report_path}")
        print(f"FP 分析已保存: {reports_dir / 'fp_analysis.json'}")


if __name__ == "__main__":
    main()
