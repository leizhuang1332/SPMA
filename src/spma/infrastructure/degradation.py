"""六级降级管理器——从 degradation/ 子包 re-export。

为兼容已有 import，所有实现已迁移到 degradation/ 子包。
"""

from spma.infrastructure.degradation.events import DegradationLevel

__all__ = ["DegradationLevel"]
