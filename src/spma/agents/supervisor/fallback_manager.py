"""分级降级管理器——L1 multi_strategy → L2 primary_backup → L3 rule_only。

主文件 ADR-002:每次请求独立降级,无跨请求状态串扰。
"""
import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


# Level 字符串常量(避免字面量散落各处,便于 grep/重构)
LEVEL_MULTI_STRATEGY = "multi_strategy"
LEVEL_PRIMARY_BACKUP = "primary_backup"
LEVEL_RULE_ONLY = "rule_only"
LEVEL_RULE_ONLY_FAILED = "rule_only_failed"


class FallbackManager:
    """三级降级:每次请求独立降级,无跨请求状态串扰。"""

    LEVELS = (LEVEL_MULTI_STRATEGY, LEVEL_PRIMARY_BACKUP, LEVEL_RULE_ONLY)

    def __init__(
        self,
        orchestrator,
        primary_backup_fn: Callable[..., Awaitable],
        rule_only_fn: Callable,
    ):
        # 早失败:async 契约校验
        if not asyncio.iscoroutinefunction(primary_backup_fn):
            raise TypeError(
                "FallbackManager.primary_backup_fn must be an async function "
                "(coroutine function), got sync function"
            )
        self._orchestrator = orchestrator
        self._primary_backup_fn = primary_backup_fn
        self._rule_only_fn = rule_only_fn
        # 监控:全局失败计数(仅用于 P6 metrics)
        self._level_failures: dict[str, int] = {l: 0 for l in self.LEVELS}

    async def execute_with_fallback(
        self,
        query: str,
        strategies: dict,
        *args,
        **kwargs,
    ) -> tuple[str, str]:
        """按 L1→L2→L3 顺序尝试,首个成功的级别返回。

        Returns:
            (result, level_used)
        """
        # L1: 多策略并行
        try:
            results = await self._orchestrator.execute_parallel(
                strategies, *args, **kwargs,
            )
            if results:
                # 默认取第一个成功的(P3 voter 会替换为投票逻辑)
                return results[0][1], LEVEL_MULTI_STRATEGY
        except Exception as e:
            self._level_failures[LEVEL_MULTI_STRATEGY] += 1
            logger.warning(f"FallbackManager L1 failed: {type(e).__name__}: {e}")

        # L2: 主备策略(由调用方注入)
        try:
            result = await self._primary_backup_fn(query, *args, **kwargs)
            if result is not None:
                return result, LEVEL_PRIMARY_BACKUP
        except Exception as e:
            self._level_failures[LEVEL_PRIMARY_BACKUP] += 1
            logger.warning(f"FallbackManager L2 failed: {type(e).__name__}: {e}")

        # L3: 纯规则兜底(用 to_thread 防止 sync 函数阻塞事件循环)
        try:
            result = await asyncio.to_thread(self._rule_only_fn, query, *args, **kwargs)
            return result, LEVEL_RULE_ONLY
        except Exception as e:
            self._level_failures[LEVEL_RULE_ONLY] += 1
            logger.error(f"FallbackManager L3 failed: {type(e).__name__}: {e}")
            return query, LEVEL_RULE_ONLY_FAILED  # 最差:返回原 query