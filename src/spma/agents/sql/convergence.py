"""确定性收敛判断——代码规则优先，LLM 兜底。

收敛条件（优先级从高到低）:
1. 行数 ∈ [1, 10000]              → 立即收敛
2. 行数 = 0 且上轮也是 0           → 收敛 + QualityReport 标记空结果
3. 行数 > 10000                   → 不收敛
4. 当前轮数 >= max_rounds          → 强制收敛
5. 耗时 >= timeout_ms              → 强制收敛
6. 以上都不满足                    → 调 LLM 语义验证（verifier.py 负责）

设计依据: SPMA-design-04 Agent收敛契约
"""

import time
from spma.agents.sql.state import SQLAgentState


def check_convergence(state: SQLAgentState) -> tuple[bool, str]:
    """检查收敛条件，返回 (是否收敛, 原因)。不调 LLM。"""
    current_round = state.get("current_round", 1)
    max_rounds = state.get("max_rounds", 5)
    row_count = state.get("row_count", 0)
    execution_success = state.get("execution_success", False)

    # 获取耗时
    start_time = state.get("start_time", 0.0)
    elapsed_ms = int((time.time() - start_time) * 1000) if start_time > 0 else 0
    timeout_ms = state.get("timeout_ms", 3000)

    # 强制终止：轮数 >= 上限
    if current_round >= max_rounds:
        return True, f"max_rounds_reached ({current_round}/{max_rounds})"

    # 强制终止：超时
    if elapsed_ms >= timeout_ms:
        return True, f"timeout ({elapsed_ms}ms >= {timeout_ms}ms)"

    if not execution_success:
        return False, "execution_failed"

    # 正常行数范围
    if 1 <= row_count <= 10000:
        return True, "deterministic: row_count in [1, 10000]"

    # 空结果两轮相同
    if row_count == 0:
        sql_history = state.get("sql_history", [])
        if len(sql_history) >= 2 and sql_history[-1] == sql_history[-2]:
            return True, "deterministic: empty_result_twice_same_sql"

    # 行数过大
    if row_count > 10000:
        return False, "too_many_rows"

    # 需要 LLM 语义验证
    return False, "need_llm_verification"
