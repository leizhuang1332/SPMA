"""评估指标计算：精确率、召回率、F1、特异度、Cohen's Kappa。"""

from typing import TypedDict


class ClassificationMetrics(TypedDict):
    precision: float
    recall: float
    f1: float
    specificity: float
    accuracy: float
    tp: int
    fp: int
    tn: int
    fn: int
    total: int


def compute_metrics(y_true: list[bool], y_pred: list[bool]) -> ClassificationMetrics:
    """计算分类指标。

    Args:
        y_true: 真实标签 (True=sufficient, False=insufficient)
        y_pred: LLM 预测标签 (True=sufficient, False=insufficient)

    Returns:
        ClassificationMetrics
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"长度不匹配: y_true={len(y_true)}, y_pred={len(y_pred)}")

    tp = sum(1 for t, p in zip(y_true, y_pred) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred) if not t and p)
    tn = sum(1 for t, p in zip(y_true, y_pred) if not t and not p)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t and not p)
    total = len(y_true)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return ClassificationMetrics(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        specificity=round(specificity, 4),
        accuracy=round(accuracy, 4),
        tp=tp, fp=fp, tn=tn, fn=fn, total=total,
    )


def compute_fold_metrics(fold_results: list[ClassificationMetrics]) -> dict:
    """聚合多折指标，输出平均±标准差。

    Args:
        fold_results: 每折的指标

    Returns:
        {"precision": {"mean": 0.82, "std": 0.02}, ...}
    """
    import statistics

    keys = ["precision", "recall", "f1", "specificity", "accuracy"]
    aggregated = {}
    for key in keys:
        values = [r[key] for r in fold_results]
        aggregated[key] = {
            "mean": round(statistics.mean(values), 4),
            "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        }
    aggregated["total_judgment_points"] = sum(r["total"] for r in fold_results)
    return aggregated


def cohens_kappa(y_true: list[bool], y_pred: list[bool]) -> float:
    """计算 Cohen's Kappa。

    Kappa = (Po - Pe) / (1 - Pe)
    """
    if len(y_true) != len(y_pred):
        raise ValueError("长度不匹配")
    n = len(y_true)
    if n == 0:
        return 0.0

    # 观察一致率
    po = sum(1 for t, p in zip(y_true, y_pred) if t == p) / n

    # 随机一致率
    p_true = sum(y_true) / n
    p_pred = sum(y_pred) / n
    pe = p_true * p_pred + (1 - p_true) * (1 - p_pred)

    if pe == 1.0:
        return 1.0

    return round((po - pe) / (1 - pe), 4)
