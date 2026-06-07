"""Agent 收敛契约的类型定义。

设计依据: SPMA-design-07 第二节 收敛契约
"""

from typing import TypedDict, Literal

ConvergenceSource = Literal[
    "deterministic",
    "llm_judged_sufficient",
    "max_rounds_reached",
    "timeout",
    "token_budget_exhausted",
    "error",
]


class ConvergenceResult(TypedDict):
    """单轮完备度判断的结果。"""

    verdict: Literal["sufficient", "insufficient"]
    source: ConvergenceSource
    reason: str
    confidence: float
    missing_info: list[str]
    suggested_actions: list[str]


class AssessmentVerdict(TypedDict):
    """完备度评估的简单二值输出——用于确定性收敛路径。"""

    sufficient: bool
    reason: str
