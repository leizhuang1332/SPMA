"""Supervisor 质量函数——Worker 输出的三维评分。

设计依据: SPMA-design-01 §2 质量函数, SPMA-design-07 §3 质量函数
"""

from spma.models.worker_output import WorkerOutput


def evaluate_worker_quality(output: WorkerOutput, query_type: str) -> float:
    """对单个 Worker 输出做三维加权评分 (0-1)。

    维度1: 结果数量 (0-0.3)
    维度2: Worker自评置信度 (0-0.3)
    维度3: 精确匹配命中 (0-0.4)

    权重矩阵按 query_type 动态调整 (data_query/search/trace)。
    """
    raise NotImplementedError


def should_reschedule(quality_scores: dict[str, float], reschedule_count: int) -> bool:
    """判断是否需要重调度——有 Worker 评分 < 0.6 且重调度 < 2 次。"""
    raise NotImplementedError


def adjust_params(
    quality_scores: dict[str, float],
    worker_outputs: list[WorkerOutput],
    failed_workers: list[str],
) -> dict:
    """重调度时调整检索参数——从成功 Worker 结果中提取桥接实体。"""
    raise NotImplementedError
