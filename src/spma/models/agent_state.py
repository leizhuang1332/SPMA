"""所有 Agent 共享的基础状态模型。

设计依据: SPMA-design-07 第五节 状态管理
"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """所有 Agent 共享的基础状态字段。

    每个 Agent 在此基础上追加自己特有的字段。
    """

    round: int
    """当前轮次编号（从 1 开始）"""

    confidence: float
    """Agent 自评信心 (0-1)。确定性收敛 ≥ 0.85，LLM 判断充足 0.6-0.85"""

    results: list[dict]
    """本轮检索/执行结果列表"""

    token_used: int
    """已消耗的 LLM 调用次数"""

    assessment_history: list[str]
    """完备度判断历史，每条为 "sufficient" 或 "insufficient: <原因>" """

    llm_calls: int
    """本轮 LLM 调用次数"""

    latency_ms: int
    """本轮累计延迟（毫秒）"""

    has_exact_match: bool
    """是否命中精确匹配实体（req_ids / table_names / code_refs）"""

    convergence_reason: str
    """收敛原因。如 "deterministic", "llm_judged_sufficient", "max_rounds_reached" """
