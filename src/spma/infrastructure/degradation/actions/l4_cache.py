"""L4: 后端检索大面积故障 → Redis 缓存热点问答兜底。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L4CacheDegradation(DegradationAction):
    """L4 缓存兜底：热点问答缓存作为主读取路径。"""

    level: DegradationLevel = "L4"

    def __init__(self, cache_service, min_cached_qa: int = 50):
        self._cache = cache_service
        self._min_cached_qa = min_cached_qa
        self.is_active = False

    async def health_check(self) -> bool:
        try:
            return not self.is_active
        except Exception:
            return False

    def _has_sufficient_cache(self) -> bool:
        return self._cache.get_cached_qa_count() >= self._min_cached_qa

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        if not self._has_sufficient_cache():
            logger.error(
                f"L4 降级缓存不足 ({self._cache.get_cached_qa_count()} < "
                f"{self._min_cached_qa})，跳过 L4 直接到 L5"
            )
            return
        logger.warning(f"L4 降级触发: {reason}，启用缓存兜底")
        self._cache.enable_fallback()
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L4 恢复: 恢复后端检索")
        self._cache.disable_fallback()
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        return True

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
