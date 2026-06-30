"""LLM 成本控制:分级模型路由 + 月度预算(主文件 ADR-008)。"""
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


_COMPLEXITY_TIER = {
    "easy": ModelTier.HAIKU,      # 指代消解(llm_semantic)
    "medium": ModelTier.SONNET,    # 扩展(context_aware)
    "hard": ModelTier.OPUS,        # 分解(llm_based)
}


class BudgetExhaustedError(Exception):
    """月度预算耗尽时抛出,调用方降级到规则路径。"""


class CostController:
    """分级 LLM 调用路由 + 预算控制。"""

    def __init__(
        self, llm_router, budget_tracker, *,
        monthly_budget_usd: float = 5000.0,
        soft_threshold: float = 0.8,
        hard_threshold: float = 0.95,
    ):
        self._llm_router = llm_router
        self._budget = budget_tracker
        self._monthly = monthly_budget_usd
        self._soft = soft_threshold
        self._hard = hard_threshold

    async def call_llm(self, prompt: str, *, complexity: str, **kwargs):
        used = await self._budget.get_month_usage_ratio()
        if used > self._hard:
            raise BudgetExhaustedError(
                f"Monthly LLM budget at {used:.1%} (>hard={self._hard:.0%}); "
                "fallback to rule-based path"
            )
        if used > self._soft:
            logger.warning("Monthly LLM budget at %s; reduce non-essential calls", f"{used:.1%}")

        tier = _COMPLEXITY_TIER.get(complexity, ModelTier.SONNET)
        result = await self._llm_router.call(tier, prompt, **kwargs)
        await self._budget.record_call(tier, len(prompt), len(result or ""))
        return result
