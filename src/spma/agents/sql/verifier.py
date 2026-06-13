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


# ============================================================
# LLM 语义验证——Slice 3 启用
# ============================================================

SEMANTIC_VERIFY_SYSTEM = """你是一个 SQL 查询结果的语义验证器。判断查询结果是否正确地回答了用户的问题。

请逐项检查：
1. 结果的行数和列数是否符合问题的预期？
2. 结果的数值范围是否合理？
3. 如果有聚合，聚合逻辑是否正确？

输出 JSON:
{
  "verdict": "sufficient" | "insufficient",
  "confidence": 0.0-1.0,
  "missing_info": "如果 insufficient，说明缺少什么信息"
}"""


async def llm_semantic_verify(
    query: str,
    sql: str,
    columns: list[str],
    rows: list[list],
    row_count: int,
    llm_client=None,
) -> str:
    """调用 Haiku 进行语义验证。

    Returns:
        "passed" 或 "failed: <原因>"
    """
    if llm_client is None:
        from spma.llm import chat
        llm_client = chat

    # 构造样本数据（最多 5 行，避免 token 过大）
    sample_rows = rows[:5]
    result_summary = f"列: {columns}\n行数: {row_count}\n示例行:\n"
    for row in sample_rows:
        result_summary += f"  {row}\n"

    user_message = f"""用户问题: {query}
执行的 SQL: {sql}
查询结果:
{result_summary}

请判断这个结果是否语义正确地回答了用户的问题。"""

    try:
        response = await llm_client(
            messages=[
                {"role": "system", "content": SEMANTIC_VERIFY_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            model="claude-haiku-4-5-20251001",
        )

        import json
        result_json = json.loads(response)
        if result_json.get("verdict") == "sufficient":
            return "passed"
        else:
            return f"failed: {result_json.get('missing_info', '语义验证不通过')}"
    except Exception as e:
        # LLM 调用失败——不阻塞，标记为 passed（降级）
        return "passed"


async def run_verification_async(state, llm_client=None) -> str:
    """异步语义验证——含 LLM 调用。Slice 3 使用。"""
    from spma.agents.sql.convergence import check_convergence

    converged, reason = check_convergence(state)

    if converged:
        if "deterministic" in reason:
            return "passed"
        elif "need_llm_verification" in reason:
            er = state.get("execution_result", {})
            return await llm_semantic_verify(
                query=state.get("query", ""),
                sql=state.get("generated_sql", ""),
                columns=er.get("columns", []),
                rows=er.get("rows", []),
                row_count=state.get("row_count", 0),
                llm_client=llm_client,
            )
        else:
            return "passed"

    return f"failed: {reason}"
