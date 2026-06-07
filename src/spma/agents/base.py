"""Agent 基类——所有 Agent 共享的基础设施方法。

设计依据: SPMA-design-07 第六节 Agent 基础设施
"""

from spma.models.agent_state import AgentState


class BaseAgent:
    """所有 Agent 的基类，提供共享的收敛判断、预算管理和检查点方法。"""

    @staticmethod
    def check_convergence(state: AgentState) -> tuple[bool, str]:
        """检查当前 Agent 是否满足收敛条件。确定性条件优先（代码规则），LLM 判断兜底。"""
        raise NotImplementedError("子类必须实现 check_convergence")

    @staticmethod
    def consume_budget(state: AgentState, tokens: int) -> bool:
        """从 Token 预算中扣减，返回是否有剩余额度。"""
        raise NotImplementedError("子类必须实现 consume_budget")

    @staticmethod
    def save_checkpoint(state: AgentState) -> None:
        """将 Agent 状态写入 LangGraph Checkpointer。"""
        raise NotImplementedError("子类必须实现 save_checkpoint")
