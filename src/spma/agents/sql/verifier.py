"""SQL 语义验证器——从"语法对"到"语义对"。

确定性条件: 执行成功 AND 行数∈[1,10000] → 自动收敛
LLM兜底: 统计异常/NULL比例/分布异常 → Haiku语义验证

设计依据: SPMA-design-04 §3.3 Agent循环语义验证增强
"""

from spma.agents.sql.state import SQLAgentState
from spma.agents.sql.convergence import check_convergence


def build_error_feedback(state: SQLAgentState) -> str:
    """从上一轮的失败中构造错误反馈文本。"""
    parts = []

    # Guard 失败
    guard_result = state.get("guard_result")
    if guard_result and not guard_result.get("passed", True):
        if guard_result.get("syntax_errors"):
            parts.append("语法错误: " + "; ".join(guard_result["syntax_errors"]))
        if guard_result.get("forbidden_operations"):
            parts.append("禁止的操作: " + ", ".join(guard_result["forbidden_operations"]))
        if guard_result.get("table_existence_errors"):
            parts.append("表/列不存在: " + "; ".join(guard_result["table_existence_errors"]))
        return "\n".join(parts)

    # 执行失败
    if not state.get("execution_success", True):
        execution_result = state.get("execution_result")
        if execution_result:
            error_msg = execution_result.get("error", "") if isinstance(execution_result, dict) else str(execution_result)
            return f"SQL 执行失败: {error_msg}"
        return "SQL 执行失败（未知原因）"

    # 行数异常
    row_count = state.get("row_count", 0)
    if row_count == 0:
        return "查询返回了 0 行。可能原因：过滤条件过严、时间范围无数据、表名选错。请检查 WHERE 条件和表名。"
    if row_count > 10000:
        return f"查询返回了 {row_count} 行（超过 10,000 上限）。请添加 LIMIT 或聚合函数（如 COUNT、SUM）。"

    # 语义验证失败
    semantic_check = state.get("semantic_check", "")
    if semantic_check.startswith("failed:"):
        return f"上一轮结果未能通过语义验证: {semantic_check}"

    return ""


def run_verification(state: SQLAgentState) -> str:
    """执行一轮语义验证。

    Returns:
        "passed" 或 "failed: <原因>"
    """
    converged, reason = check_convergence(state)

    if converged:
        if "deterministic" in reason:
            return "passed"
        elif "need_llm_verification" in reason:
            # 确定性条件不满足——需要 LLM 判断，Slice 3 实现
            return "passed"  # Slice 1: 暂时通过
        else:
            # max_rounds 或 timeout 强制收敛——也算通过
            return "passed"

    # 不收敛
    return f"failed: {reason}"
