"""离线策略评估 + EMA 权重进化(主文件 §3.5 + ADR-005/006)。

复用已有 write_weights_snapshot 写入 qr_weights_history。
"""
import logging
from typing import Awaitable, Callable

from spma.agents.supervisor.qr_state import write_weights_snapshot

logger = logging.getLogger(__name__)


class StableStrategyEvaluator:
    """带 EMA + min_weight 边界约束的离线评估器。"""

    def __init__(
        self,
        db_pool,
        ema_alpha: float = 0.1,
        min_weight: float = 0.1,
        review_delta_threshold: float = 0.1,
    ):
        self._pool = db_pool
        self._ema_alpha = ema_alpha
        self._min_weight = min_weight
        self._review_threshold = review_delta_threshold

    async def evaluate_and_propose(
        self,
        test_cases: list[dict],
        current_weights: dict[str, float],
        evaluator: Callable[[dict], Awaitable[dict[str, float]]],
    ) -> dict:
        """评估 + 提出新权重(写入 qr_weights_history 但不应用)。

        Returns:
            {
                "evaluation": {strategy_name: {"avg_score": ..., "count": ...}, ...},
                "weight_diffs": {strategy_name: {"old": x, "new": y, "delta": z}, ...},
                "snapshot_id": int,
                "should_review": bool,
            }
        """
        logger.info(
            "StableStrategyEvaluator.evaluate_and_propose: %d test cases",
            len(test_cases),
        )

        # 1) 评估
        results: dict[str, list[float]] = {n: [] for n in current_weights}
        known_strategies = set(current_weights.keys())
        fail_count = 0
        for case in test_cases:
            try:
                scores = await evaluator(case)
                if scores is None:
                    continue
                # 检测未知 strategy → warning + 跳过
                extra = set(scores.keys()) - known_strategies
                if extra:
                    logger.warning(
                        "evaluator returned unknown strategies %s, ignoring",
                        extra,
                    )
                for n, s in scores.items():
                    if n in results:
                        results[n].append(float(s))
            except Exception as e:
                fail_count += 1
                # PII 安全:case 可能含用户 query,不完整 dump
                case_id = (
                    case.get("query", "<no_query>")
                    if isinstance(case, dict)
                    else "<invalid>"
                )
                logger.warning(
                    "evaluator failed for case %s: %s",
                    case_id,
                    type(e).__name__,
                    exc_info=True,
                )
                continue

        if fail_count > 0:
            logger.warning(
                "evaluator: %d/%d cases failed",
                fail_count,
                len(test_cases),
            )

        eval_summary = {
            n: {"avg_score": sum(s) / len(s) if s else 0.0, "count": len(s)}
            for n, s in results.items()
        }

        # 2) EMA + min_weight
        diffs = {}
        for n, current_w in current_weights.items():
            target_w = eval_summary[n]["avg_score"]
            new_w = (1 - self._ema_alpha) * current_w + self._ema_alpha * target_w
            new_w = max(self._min_weight, new_w)
            diffs[n] = {"old": current_w, "new": new_w, "delta": new_w - current_w}

        # 归一化
        total = sum(d["new"] for d in diffs.values())
        if total > 0:
            for d in diffs.values():
                d["new"] /= total

        # 3) 审核触发
        total_delta = sum(abs(d["delta"]) for d in diffs.values())
        should_review = total_delta > self._review_threshold

        logger.info(
            "StableStrategyEvaluator: total_delta=%.4f, should_review=%s",
            total_delta,
            should_review,
        )

        # 4) 写入 qr_weights_history(不应用)—— 若抛错则上抛(不静默吞)
        snapshot_id = await write_weights_snapshot(
            self._pool,
            payload={"weights": {n: d["new"] for n, d in diffs.items()}},
            source="ema",
            applied_at=None,
        )

        logger.info(
            "StableStrategyEvaluator: snapshot_id=%d written",
            snapshot_id,
        )

        return {
            "evaluation": eval_summary,
            "weight_diffs": diffs,
            "snapshot_id": snapshot_id,
            "should_review": should_review,
        }
