"""六级降级管理器——L0(全功能) → L5(静态FAQ)。

每级配置: trigger条件 + actions动作 + auto_recovery检查间隔 + recovery条件

设计依据: SPMA-design-06 §4 多级降级策略 + API-06 §5 降级配置契约
"""

from typing import Literal

DegradationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

DEGRADATION_CONFIG: dict[DegradationLevel, dict] = {
    "L0": {"trigger": [], "actions": ["全功能 5 Agent 多轮循环"], "auto_recovery_sec": 0},
    "L1": {"trigger": ["主LLM超时率>10%", "主LLM 5xx>5%"],
           "actions": ["切Qwen3-8B", "完备度→确定性条件"],
           "auto_recovery_sec": 30},
    "L2": {"trigger": ["Agent P99延迟恶化>50%", "Token成本恶化>100%"],
           "actions": ["Agent→pipeline模式"],
           "auto_recovery_sec": 60},
    "L3": {"trigger": ["向量库不可用", "向量检索P99>500ms"],
           "actions": ["纯BM25检索"],
           "auto_recovery_sec": 30},
    "L4": {"trigger": ["后端检索大面积故障"],
           "actions": ["Redis缓存热点问答"],
           "auto_recovery_sec": 30},
    "L5": {"trigger": ["所有动态服务不可用"],
           "actions": ["静态FAQ + 联系管理员"],
           "auto_recovery_sec": 60},
}

class DegradationManager:
    """降级状态机——管理 L0↔L5 切换。"""

    def current_level(self) -> DegradationLevel:
        raise NotImplementedError

    def check_and_degrade(self) -> bool:
        raise NotImplementedError

    def check_and_recover(self) -> bool:
        raise NotImplementedError

    def manual_degrade(self, level: DegradationLevel, reason: str) -> None:
        raise NotImplementedError

    def manual_recover(self) -> None:
        raise NotImplementedError
