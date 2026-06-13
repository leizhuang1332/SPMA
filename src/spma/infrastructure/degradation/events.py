"""降级系统事件定义。"""

from dataclasses import dataclass, field
from typing import Literal
import time

DegradationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]


@dataclass
class DegradationEvent:
    """降级/恢复事件。"""
    event_type: Literal["degradation.triggered", "degradation.recovered", "degradation.manual"]
    level: DegradationLevel
    reason: str
    timestamp: float = field(default_factory=time.time)
    previous_level: DegradationLevel | None = None
    triggered_by: Literal["auto", "manual"] = "auto"
    operator: str | None = None  # 手动触发时的操作人


@dataclass
class RecoveryEvent:
    """自动恢复事件。"""
    from_level: DegradationLevel
    to_level: DegradationLevel
    reason: str
    timestamp: float = field(default_factory=time.time)
    checks_passed: int = 0  # 连续健康检查通过次数

    @property
    def level(self) -> DegradationLevel:
        """恢复到目标级别，等同于 level 语义，供统一的事件处理使用。"""
        return self.to_level
