"""L3: 向量数据库不可用 → 切换纯 BM25 检索。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L3RetrievalDegradation(DegradationAction):
    """L3 检索降级：向量检索→纯 BM25 关键词检索。"""

    level: DegradationLevel = "L3"

    def __init__(self, retrieval_router):
        self._router = retrieval_router
        self.is_active = False

    async def health_check(self) -> bool:
        try:
            return self._router.vector_enabled
        except Exception:
            return False

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L3 降级触发: {reason}，切换到纯 BM25 检索")
        self._router.vector_enabled = False
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L3 恢复: 重新启用向量检索")
        self._router.vector_enabled = True
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        try:
            return self._router.vector_enabled
        except Exception:
            return False

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
