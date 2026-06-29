"""策略编排器——多路并行 + 异常隔离 + 熔断保护。

基于已有 src/spma.infrastructure.circuit_breaker,不重新发明轮子。
"""
import asyncio
import logging
from typing import Awaitable, Callable

from spma.infrastructure.circuit_breaker import (
    CircuitBreakerOpenError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)


class StrategyOrchestrator:
    """策略编排器:统一管理多路策略的生命周期、并行调度、熔断集成。"""

    def __init__(self, stage: str, names: list[str]):
        self._stage = stage
        # 每个 strategy 分配独立 CB(全局注册表内通过 name 区分)
        self._breakers: dict[str, "CircuitBreaker"] = {
            name: get_circuit_breaker(f"qr_{stage}_{name}") for name in names
        }

    async def execute_parallel(
        self,
        strategies: dict[str, Callable[..., Awaitable]],
        *args,
        **kwargs,
    ) -> list[tuple[str, object]]:
        """并行执行所有策略,收集 (name, result) 元组。

        行为:
        - 任一策略被熔断 → 跳过,不参与本次调用
        - 任一策略抛异常 → 记录警告 + 返回 None(不影响其他策略)
        - 全部策略返回 None 或熔断 → 返回空列表(由 FallbackManager 兜底)

        Returns:
            [(strategy_name, result), ...],已过滤 None。
        """
        # 先过滤熔断的策略(避免 gather 包含被熔断的)
        active = {
            name: fn for name, fn in strategies.items()
            if not self._breakers[name].is_open()
        }
        if not active:
            return []

        coros = [
            self._safe_invoke(name, fn, *args, **kwargs)
            for name, fn in active.items()
        ]
        # 关键(对应主文件 ADR-003):并行,不串行
        results = await asyncio.gather(*coros)
        return [
            (name, result)
            for (name, _), result in zip(active.items(), results)
            if result is not None
        ]

    async def _safe_invoke(self, name, fn, *args, **kwargs):
        """熔断保护 + 异常隔离。"""
        cb = self._breakers[name]
        try:
            return await cb.call(lambda: fn(*args, **kwargs))
        except CircuitBreakerOpenError as e:
            logger.debug(
                f"[{self._stage}] strategy {name} circuit-open, "
                f"skipped ({e.retry_after_seconds:.0f}s left)"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[{self._stage}] strategy {name} failed: "
                f"{type(e).__name__}: {e}",
                exc_info=False,
            )
            return None
