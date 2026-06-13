"""六级降级管理体系。

导出: DegradationManager, DegradationLevel, DegradationEvent, RecoveryEvent
"""

from spma.infrastructure.degradation.events import (
    DegradationEvent,
    DegradationLevel,
    RecoveryEvent,
)

__all__ = [
    "DegradationLevel",
    "DegradationEvent",
    "RecoveryEvent",
]
