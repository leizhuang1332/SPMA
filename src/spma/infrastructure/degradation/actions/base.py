"""降级动作抽象基类。"""

from abc import ABC, abstractmethod
from spma.infrastructure.degradation.events import DegradationLevel


class DegradationAction(ABC):
    """单个降级级别的策略基类。每个具体类实现一级降级。

    约束:
    - execute() 必须幂等
    - recover() 只能从当前级别恢复
    - health_check() 返回 True=正常, False=异常
    """

    level: DegradationLevel

    @abstractmethod
    async def health_check(self) -> bool:
        """检查该级别依赖是否健康。返回 True=正常。"""
        ...

    @abstractmethod
    async def execute(self, reason: str) -> None:
        """执行降级动作。幂等——重复调用安全。"""
        ...

    @abstractmethod
    async def recover(self) -> bool:
        """尝试恢复。返回 True=恢复成功。"""
        ...

    @abstractmethod
    def recovery_conditions_met(self) -> bool:
        """检查自动恢复条件是否满足（同步，无副作用）。"""
        ...

    @property
    @abstractmethod
    def recovery_check_interval_seconds(self) -> int:
        """恢复检查间隔（秒）。"""
        ...
