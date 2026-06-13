"""L5: 所有动态服务不可用 → 返回预定义 FAQ + 提示联系管理员。"""

from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel
import logging

logger = logging.getLogger(__name__)

DEFAULT_FAQ = {
    "faq": [
        {
            "q": "系统暂时不可用怎么办？",
            "a": "当前系统正在维护中，请稍后重试。如紧急需要，请联系管理员。",
        },
        {
            "q": "如何联系管理员？",
            "a": "请发送邮件至 admin@company.com 或在企业微信中搜索「SPMA 运维」。",
        },
    ],
    "message": "系统当前不可用，以下为常见问题解答。如需帮助，请联系管理员。",
}


class L5StaticFallback(DegradationAction):
    """L5 静态兜底：返回预定义 FAQ。"""

    level: DegradationLevel = "L5"

    def __init__(self, faq_json: dict | None = None):
        self._faq = faq_json or DEFAULT_FAQ
        self.is_active = False

    async def health_check(self) -> bool:
        """L5 激活时，系统仍不可用。"""
        return not self.is_active

    async def execute(self, reason: str) -> None:
        if not self.is_active:
            logger.critical(f"L5 静态兜底激活: {reason}")
            self.is_active = True

    async def recover(self) -> bool:
        if self.is_active:
            logger.info("L5 静态兜底恢复")
            self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        return not self.is_active

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 60

    def get_faq(self) -> dict:
        return self._faq
