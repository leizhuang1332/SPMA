"""Token 预算追踪器——跨 Agent 共享。

预算按 query_type 分配: 单源简单8次, 单源复杂12次, 跨源20次, 三源全查25次
每次 LLM 调用前 consume()，超限抛 TokenBudgetExhausted。

设计依据: SPMA-design-06 §9 Token预算管理
"""

class TokenBudgetExhausted(Exception):
    """Token 预算耗尽异常。"""
    pass


class TokenBudgetTracker:
    """Token 预算追踪器（跨 Agent 共享）。"""

    def consume(self, amount: int, agent_type: str) -> bool:
        raise NotImplementedError

    def remaining(self) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError
