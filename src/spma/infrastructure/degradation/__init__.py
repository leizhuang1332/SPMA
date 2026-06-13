"""六级降级管理体系。

导出: DegradationManager, DegradationLevel, DegradationEvent, RecoveryEvent
"""

from spma.infrastructure.degradation.events import (
    DegradationEvent,
    DegradationLevel,
    RecoveryEvent,
)
from spma.infrastructure.degradation.manager import DegradationManager

__all__ = [
    "DegradationManager",
    "DegradationLevel",
    "DegradationEvent",
    "RecoveryEvent",
]
