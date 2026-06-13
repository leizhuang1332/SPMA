"""自动恢复检测——定时检查恢复条件 + 逐级恢复。"""

import asyncio
import logging
from typing import Awaitable, Callable
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)

LEVEL_ORDER: list[DegradationLevel] = ["L0", "L1", "L2", "L3", "L4", "L5"]


class DegradationRecovery:
    """自动恢复检测——检查恢复条件，逐级恢复。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        on_recover: Callable[[DegradationLevel, DegradationLevel, str], Awaitable[None]],
    ):
        self._actions = {a.level: a for a in actions}
        self._on_recover = on_recover
        self._running = False
        self._task: asyncio.Task | None = None

    async def run_loop(self, get_current_level) -> None:
        """定期检查恢复条件。"""
        self._running = True
        logger.info("自动恢复检测启动")
        while self._running:
            try:
                current = get_current_level()
                await self._check_once(current)
            except Exception:
                logger.exception("自动恢复检测异常")
            current = get_current_level()
            action = self._actions.get(current)
            interval = action.recovery_check_interval_seconds if action else 30
            await asyncio.sleep(interval)

    async def _check_once(self, current_level: DegradationLevel) -> None:
        """检查当前级别是否可以恢复到上一级。"""
        if current_level == "L0":
            return

        action = self._actions.get(current_level)
        if action and action.recovery_conditions_met():
            idx = LEVEL_ORDER.index(current_level)
            target = LEVEL_ORDER[idx - 1] if idx > 0 else "L0"
            logger.info(f"恢复条件满足: {current_level} → {target}")
            await self._on_recover(current_level, target, "自动恢复条件满足")

    async def stop(self) -> None:
        """停止恢复检测循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("自动恢复检测已停止")
