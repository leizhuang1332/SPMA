"""L1: 主 LLM 不可用 → 切换到本地 Qwen3-8B + 完备度判断降级为确定性条件。"""

import logging
import time
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "claude-sonnet"
FALLBACK_MODEL = "qwen3-8b-local"


class L1LLMDegradation(DegradationAction):
    """L1 LLM 降级：主模型→本地模型。"""

    level: DegradationLevel = "L1"

    def __init__(self, llm_client, required_consecutive_pings: int = 3,
                 min_recovery_interval_seconds: float = 60.0):
        self._client = llm_client
        self.is_active = False
        self._required_pings = required_consecutive_pings
        self._min_interval = min_recovery_interval_seconds
        self._consecutive_ok = 0
        self._last_check_time = 0.0

    async def health_check(self) -> bool:
        """检查主 LLM 是否可用。"""
        self._last_check_time = time.time()
        try:
            ok = await self._client.ping()
            if ok:
                self._consecutive_ok += 1
            else:
                self._consecutive_ok = 0
            return ok
        except Exception:
            self._consecutive_ok = 0
            return False

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L1 降级触发: {reason}，切换到 {FALLBACK_MODEL}")
        self._client.set_model(FALLBACK_MODEL)
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info(f"L1 恢复: 切换回 {PRIMARY_MODEL}")
        self._client.set_model(PRIMARY_MODEL)
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        return (
            self._consecutive_ok >= self._required_pings
            and (time.time() - self._last_check_time) >= self._min_interval
        )

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
