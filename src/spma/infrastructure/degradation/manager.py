"""降级管理器——状态机编排层。

管理 L0↔L5 六级别切换，协调 actions/trigger/recovery。
支持手动降级（可跨级）、自动降级（逐级）、自动恢复（逐级）。

设计依据: Phase 4 hardening design spec §3
"""

import asyncio
import logging
from typing import Callable, Awaitable
from spma.infrastructure.degradation.events import (
    DegradationLevel,
    DegradationEvent,
    RecoveryEvent,
)
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.trigger import DegradationTrigger
from spma.infrastructure.degradation.recovery import DegradationRecovery

logger = logging.getLogger(__name__)

LEVEL_ORDER: list[DegradationLevel] = ["L0", "L1", "L2", "L3", "L4", "L5"]


class DegradationManager:
    """降级状态机：管理 L0↔L5 切换，协调 actions、trigger、recovery。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        auto_recovery_enabled: bool = True,
    ):
        self._state: DegradationLevel = "L0"
        self._actions: dict[DegradationLevel, DegradationAction] = {
            a.level: a for a in actions
        }
        self._history: list[DegradationEvent | RecoveryEvent] = []
        self._auto_recovery_enabled = auto_recovery_enabled
        self._running = False

        self._trigger = DegradationTrigger(actions, self._handle_auto_degrade)
        self._recovery = DegradationRecovery(actions, self._handle_auto_recover)

        # 事件回调（供外部注册，如 AuditLogger）
        self.on_event: Callable | None = None

    @property
    def current_level(self) -> DegradationLevel:
        return self._state

    @property
    def auto_recovery_enabled(self) -> bool:
        return self._auto_recovery_enabled

    async def start(self) -> None:
        """启动后台检查循环（trigger + recovery）。"""
        if self._running:
            return
        self._running = True
        logger.info("DegradationManager 启动")
        self._trigger._task = asyncio.create_task(self._trigger.run_loop())
        self._recovery._task = asyncio.create_task(
            self._recovery.run_loop(lambda: self._state)
        )

    async def stop(self) -> None:
        """优雅停止。"""
        self._running = False
        await self._trigger.stop()
        await self._recovery.stop()
        logger.info("DegradationManager 停止")

    async def manual_degrade(
        self, level: DegradationLevel, reason: str, operator: str = "admin"
    ) -> None:
        """手动触发降级，支持跨级。"""
        if level == "L0":
            await self.manual_recover()
            return

        target_idx = LEVEL_ORDER.index(level)
        current_idx = LEVEL_ORDER.index(self._state)
        if target_idx <= current_idx and self._state != "L0":
            logger.info(f"已在 {self._state} 或更高级别，跳过降级到 {level}")
            return

        logger.warning(f"手动降级: {self._state} → {level} ({reason}, by {operator})")
        previous = self._state
        await self._execute_actions_up_to(level, reason)

        event = DegradationEvent(
            event_type="degradation.manual",
            level=level,
            reason=reason,
            previous_level=previous,
            triggered_by="manual",
            operator=operator,
        )
        self._state = level
        self._history.append(event)
        self._emit(event)

    async def manual_recover(self) -> None:
        """手动恢复——逐级恢复到 L0。"""
        if self._state == "L0":
            return
        logger.info(f"手动恢复: {self._state} → L0")
        previous = self._state
        await self._recover_all()
        event = DegradationEvent(
            event_type="degradation.manual",
            level="L0",
            reason="手动恢复",
            previous_level=previous,
            triggered_by="manual",
            operator="admin",
        )
        self._state = "L0"
        self._history.append(event)
        self._emit(event)

    def get_status(self) -> dict:
        """返回当前降级状态（管理 API 数据源）。"""
        current_idx = LEVEL_ORDER.index(self._state)
        return {
            "current_level": self._state,
            "degraded_components": [
                f"level_{level}"
                for level in LEVEL_ORDER[1 : current_idx + 1]
            ],
            "active_degradations": [
                {"level": level, "trigger": "see history"}
                for level in LEVEL_ORDER[1 : current_idx + 1]
            ],
            "last_degradation_at": self._last_event_time("degradation"),
            "last_recovery_at": self._last_event_time("recovery"),
            "auto_recovery_enabled": self._auto_recovery_enabled,
        }

    def get_history(self, limit: int = 50) -> list:
        """返回最近的降级/恢复事件。"""
        return self._history[-limit:]

    async def _handle_auto_degrade(self, level: DegradationLevel, reason: str) -> None:
        """自动降级回调（从 trigger 调用）。"""
        if LEVEL_ORDER.index(level) <= LEVEL_ORDER.index(self._state):
            return

        logger.warning(f"自动降级: {self._state} → {level} ({reason})")
        previous = self._state
        await self._execute_actions_up_to(level, reason)

        event = DegradationEvent(
            event_type="degradation.triggered",
            level=level,
            reason=reason,
            previous_level=previous,
            triggered_by="auto",
        )
        self._state = level
        self._history.append(event)
        self._emit(event)

    async def _handle_auto_recover(
        self, from_level: DegradationLevel, to_level: DegradationLevel, reason: str
    ) -> None:
        """自动恢复回调（从 recovery 调用）。"""
        action = self._actions.get(from_level)
        if action:
            success = await action.recover()
            if not success:
                logger.warning(f"恢复 {from_level} 失败")
                return

        event = RecoveryEvent(
            from_level=from_level,
            to_level=to_level,
            reason=reason,
        )
        self._state = to_level
        self._history.append(event)
        self._emit(event)
        logger.info(f"自动恢复: {from_level} → {to_level}")

    async def _execute_actions_up_to(
        self, target_level: DegradationLevel, reason: str
    ) -> None:
        """执行从 L1 到目标级别之间的所有降级动作（叠加）。"""
        target_idx = LEVEL_ORDER.index(target_level)
        for i in range(1, target_idx + 1):
            level = LEVEL_ORDER[i]
            action = self._actions.get(level)
            if action:
                await action.execute(reason)

    async def _recover_all(self) -> None:
        """执行所有活跃级别的恢复动作（从高到低）。"""
        current_idx = LEVEL_ORDER.index(self._state)
        for i in range(current_idx, 0, -1):
            level = LEVEL_ORDER[i]
            action = self._actions.get(level)
            if action:
                await action.recover()

    def _emit(self, event) -> None:
        """发送事件到外部监听器。"""
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                logger.exception("事件回调失败")

    def _last_event_time(self, event_type_hint: str) -> float | None:
        """获取指定类型最近事件的时间。"""
        for event in reversed(self._history):
            if event_type_hint == "recovery" and isinstance(event, RecoveryEvent):
                return event.timestamp
            if event_type_hint == "degradation" and isinstance(event, DegradationEvent):
                if event.level != "L0":
                    return event.timestamp
        return None
