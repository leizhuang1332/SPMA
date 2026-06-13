"""降级触发器——双入口：内建健康检查循环 + Prometheus webhook 预留。"""

import asyncio
import logging
from typing import Awaitable, Callable
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class DegradationTrigger:
    """降级触发器——内建轮询 + Prometheus webhook。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        on_degrade: Callable[[DegradationLevel, str], Awaitable[None]],
        poll_interval: float = 30.0,
    ):
        self._actions = {a.level: a for a in actions}
        self._on_degrade = on_degrade
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def run_loop(self) -> None:
        """入口1：内建健康检查循环。每 poll_interval 秒轮询所有 action。"""
        self._running = True
        logger.info(
            f"降级触发器启动，间隔={self._poll_interval}s，"
            f"监控级别={list(self._actions.keys())}"
        )
        while self._running:
            try:
                await self._check_once()
            except Exception:
                logger.exception("降级触发器轮询异常")
            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> None:
        """执行一次全量健康检查。"""
        for level, action in self._actions.items():
            if level == "L0":
                continue
            is_healthy = await action.health_check()
            if not is_healthy:
                logger.warning(
                    f"级别 {level} 健康检查失败，触发降级"
                )
                await self._on_degrade(level, f"健康检查失败: {level}")

    async def handle_webhook(self, alert: dict) -> None:
        """入口2：Prometheus AlertManager webhook（预留）。"""
        alerts = alert.get("alerts", [])
        for a in alerts:
            labels = a.get("labels", {})
            level = labels.get("degradation_level")
            if level and level in self._actions:
                summary = a.get("annotations", {}).get("summary", "Prometheus alert")
                await self._on_degrade(level, summary)

    async def stop(self) -> None:
        """停止轮询循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("降级触发器已停止")
