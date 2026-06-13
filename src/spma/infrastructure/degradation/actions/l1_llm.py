"""L1: 主 LLM 不可用 → 切换到 fallback role 的 provider+model。

动态获取当前 generation role 的配置，降级时切到 fallback role，
恢复时切回原 provider+model。
"""

import logging
import time
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L1LLMDegradation(DegradationAction):
    """L1 LLM 降级：主模型→fallback 模型（动态读取 role 配置）。"""

    level: DegradationLevel = "L1"

    def __init__(self, required_consecutive_pings: int = 3,
                 min_recovery_interval_seconds: float = 60.0):
        self.is_active = False
        self._required_pings = required_consecutive_pings
        self._min_interval = min_recovery_interval_seconds
        self._consecutive_ok = 0
        self._last_check_time = 0.0
        self._original_provider: str | None = None
        self._original_model: str | None = None

    async def health_check(self) -> bool:
        """检查当前 generation role 的 provider 是否可用。"""
        from spma.llm.router import LLMRouter

        self._last_check_time = time.time()
        try:
            router = LLMRouter.get_instance()
            gen_cfg = router.get_role_config("generation")
            if gen_cfg is None:
                return True

            provider = router._providers.get(gen_cfg.provider)
            if provider is None:
                return False

            ok = await provider.ping()
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

        from spma.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        gen_cfg = router.get_role_config("generation")
        fallback_cfg = router.get_role_config("fallback")

        if gen_cfg is None or fallback_cfg is None:
            logger.warning("L1 降级跳过: generation 或 fallback role 未配置")
            return

        self._original_provider = gen_cfg.provider
        self._original_model = gen_cfg.model

        router.set_role("generation", fallback_cfg.provider, fallback_cfg.model)
        self.is_active = True
        logger.warning(
            f"L1 降级触发: {reason}，generation {self._original_provider}/{self._original_model} "
            f"→ {fallback_cfg.provider}/{fallback_cfg.model}"
        )

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        if self._original_provider is None or self._original_model is None:
            return False

        from spma.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        router.set_role("generation", self._original_provider, self._original_model)
        self.is_active = False
        logger.info(f"L1 恢复: generation 切回 {self._original_provider}/{self._original_model}")
        return True

    def recovery_conditions_met(self) -> bool:
        return (
            self._consecutive_ok >= self._required_pings
            and (time.time() - self._last_check_time) >= self._min_interval
        )

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
