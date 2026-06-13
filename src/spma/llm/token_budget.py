"""跨 Agent Token 预算管理。

设计依据: SPMA-design-06 §9 Token预算管理
"""

BUDGET_MATRIX = {
    "single_simple":   {"total": 8,  "supervisor": 2, "workers": 4,  "synthesis": 2},
    "single_complex":  {"total": 12, "supervisor": 3, "workers": 6,  "synthesis": 3},
    "cross_source":    {"total": 20, "supervisor": 4, "workers": 12, "synthesis": 4},
    "three_source":    {"total": 25, "supervisor": 5, "workers": 15, "synthesis": 5},
}


class TokenBudgetExhausted(Exception):
    """Token 预算耗尽异常。"""
    pass


class TokenBudgetManager:
    """跨 Agent Token 预算管理器。

    按 query_type + num_sources 选择预算档位，track_call 追踪调用次数，
    remaining 返回指定 Agent 的剩余预算。
    """

    def __init__(self, query_type: str, num_sources: int):
        budget_key = "single_simple"
        if num_sources >= 3:
            budget_key = "three_source"
        elif num_sources >= 2:
            budget_key = "cross_source"
        elif query_type in ("data_query", "trace"):
            budget_key = "single_complex"
        self._budget = dict(BUDGET_MATRIX[budget_key])
        self._used: dict[str, int] = {"supervisor": 0, "workers": 0, "synthesis": 0}

    def track_call(self, agent: str, count: int = 1) -> bool:
        """记录一次 LLM 调用，若超限返回 False。"""
        self._used[agent] = self._used.get(agent, 0) + count
        return self._used[agent] <= self._budget.get(agent, 10)

    def remaining(self, agent: str) -> int:
        """返回指定 Agent 的剩余调用次数。"""
        return max(0, self._budget.get(agent, 0) - self._used.get(agent, 0))

    @property
    def budget(self) -> dict:
        return dict(self._budget)

    @property
    def used(self) -> dict[str, int]:
        return dict(self._used)
