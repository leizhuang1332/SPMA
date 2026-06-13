"""L2: Agent 延迟恶化/Token成本爆炸 → 通过 Feature Flag 回退到 pipeline 模式。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)

AGENT_FLAGS = [
    "doc_agentic", "code_agentic", "sql_agentic",
    "supervisor_agentic", "synth_agentic",
]


class L2AgentDegradation(DegradationAction):
    """L2 Agent 降级：Agent→单轮 pipeline。"""

    level: DegradationLevel = "L2"

    def __init__(self, feature_flag_service):
        self._ff = feature_flag_service
        self.is_active = False
        self._rolled_back: set[str] = set()

    async def health_check(self) -> bool:
        return not self.is_active

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L2 降级触发: {reason}，回退所有 Agent 到 pipeline 模式")
        for flag in AGENT_FLAGS:
            if self._ff.is_enabled(flag):
                await self._ff.update_flag(flag, False, reason, "degradation_system")
                self._rolled_back.add(flag)
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L2 恢复: 恢复所有 Agent 的 agentic 模式")
        for flag in list(self._rolled_back):
            await self._ff.update_flag(flag, True, "auto_recovery", "degradation_system")
        self._rolled_back.clear()
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        return True

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 60
