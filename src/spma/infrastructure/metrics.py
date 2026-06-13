"""基础设施指标——不依赖 Prometheus SDK，暴露 gauge/getter 格式。

后续集成 Prometheus 时直接调用 as_prometheus_gauges() 即可。
"""

from dataclasses import dataclass, field
from typing import Literal
import time

CircuitStateLabel = Literal["closed", "open", "half_open"]
DegradationLevelLabel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

_LEVEL_TO_INT: dict[DegradationLevelLabel, int] = {
    "L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5,
}


@dataclass
class DegradationMetrics:
    """降级系统指标。"""
    current_level: DegradationLevelLabel = "L0"
    degradation_count_total: int = 0
    last_degradation_at: float | None = None
    last_recovery_at: float | None = None
    time_in_current_level_seconds: float = 0.0

    def record_degradation(self, level: DegradationLevelLabel) -> None:
        self.current_level = level
        self.degradation_count_total += 1
        self.last_degradation_at = time.time()

    def record_recovery(self, level: DegradationLevelLabel) -> None:
        self.current_level = level
        self.last_recovery_at = time.time()

    def as_prometheus_gauges(self) -> dict[str, float]:
        """返回 {metric_name: value}，供 Prometheus exporter 使用。"""
        return {
            "spma_degradation_level": _LEVEL_TO_INT[self.current_level],
            "spma_degradation_count_total": float(self.degradation_count_total),
        }


@dataclass
class AgentMetrics:
    """Agent 专用指标——各 Agent 的延迟、成本、信心率。"""
    agent_rounds_p99: float = 0.0
    agent_false_confidence_rate: float = 0.0
    agent_early_stop_rate: float = 0.0
    agent_degradation_rate: float = 0.0
    supervisor_reschedule_rate: float = 0.0
    supervisor_timeout_rate: float = 0.0

    def as_prometheus_gauges(self) -> dict[str, float]:
        return {
            "spma_agent_rounds_p99": self.agent_rounds_p99,
            "spma_agent_false_confidence_rate": self.agent_false_confidence_rate,
            "spma_agent_degradation_rate": self.agent_degradation_rate,
            "spma_supervisor_reschedule_rate": self.supervisor_reschedule_rate,
            "spma_supervisor_timeout_rate": self.supervisor_timeout_rate,
        }


# 全局单例
degradation_metrics = DegradationMetrics()
agent_metrics = AgentMetrics()
