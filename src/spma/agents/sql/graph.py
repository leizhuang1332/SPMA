"""SQL Agent 的 LangGraph StateGraph 定义。

节点: generate(LLM SQL生成) → guard(SQL Guard) → execute(只读执行) → verify(语义验证)
条件边: guard失败→带错误回到generate / verify不通过→带异常回到generate / 通过→END

设计依据: SPMA-design-04 Agent循环图
"""

import time
from typing import Literal

from langgraph.graph import StateGraph, END

from spma.agents.sql.state import SQLAgentState, SchemaHit
from spma.agents.sql.guard import run_full_guard
from spma.agents.sql.generator import generate_sql
from spma.agents.sql.verifier import build_error_feedback, run_verification
from spma.agents.sql.convergence import check_convergence


# 全局 Schema 快照（Slice 1: 硬编码；Slice 2: 从 PGVector/内存缓存加载）
_SCHEMA_SNAPSHOT: dict[str, set[str]] = {}


def set_schema_snapshot(snapshot: dict[str, set[str]]) -> None:
    """设置全局 Schema 快照（用于 Guard L3）。"""
    global _SCHEMA_SNAPSHOT
    _SCHEMA_SNAPSHOT = snapshot


def _mock_rag(state: SQLAgentState) -> list[SchemaHit]:
    """Mock Schema RAG——Slice 1 使用硬编码 Schema。Slice 2 替换为真实 RAG。"""
    hits = []
    for table_name, columns in _SCHEMA_SNAPSHOT.items():
        hits.append(SchemaHit(
            table_name=table_name,
            ddl=f"CREATE TABLE {table_name} (...)",
            columns=[
                {"column_name": col, "data_type": "text",
                 "is_nullable": True, "comment": None,
                 "business_meaning": None, "enum_values": None}
                for col in columns
            ],
            foreign_keys=[],
            business_description=f"{table_name} 表",
            few_shot_queries=[],
            relevance_score=1.0,
        ))
    return hits[:5]


async def generate_node(state: SQLAgentState) -> dict:
    """generate 节点: LLM SQL 生成。"""
    if progress:
        await progress.publish_step("sql_worker", "generating", "正在生成 SQL…")
    state["current_round"] = state.get("current_round", 0) + 1
    if "start_time" not in state or state["start_time"] == 0:
        state["start_time"] = time.time()

    # 构造错误反馈
    error_feedback = build_error_feedback(state)

    # Schema RAG（Slice 1: Mock）
    schema_hits = _mock_rag(state)
    state["schema_search_results"] = schema_hits

    # 调用 LLM 生成 SQL
    generated_sql = await generate_sql(
        query=state.get("query", ""),
        schema_hits=schema_hits,
        error_feedback=error_feedback,
    )
    state["generated_sql"] = generated_sql

    sql_history = state.get("sql_history", [])
    sql_history.append(generated_sql)
    state["sql_history"] = sql_history

    return state


async def guard_node(state: SQLAgentState) -> dict:
    """guard 节点: 执行 L1-L4 SQL Guard 校验。"""
    if progress:
        await progress.publish_step("sql_worker", "guarding", "正在安全检查…")
    sql = state.get("generated_sql", "")
    result = run_full_guard(sql, _SCHEMA_SNAPSHOT)
    state["guard_result"] = result
    state["guard_passed"] = result["passed"]
    return state


async def execute_node(state: SQLAgentState) -> dict:
    """execute 节点: 执行 SQL。"""
    if progress:
        await progress.publish_step("sql_worker", "executing", "正在执行查询…")
    session_executor = state.get("_executor")
    if session_executor is None:
        state["execution_success"] = False
        state["execution_result"] = {"error": "执行器未初始化", "columns": [], "rows": [], "row_count": 0}
        return state

    try:
        result = session_executor.execute(state.get("generated_sql", ""))
        state["execution_result"] = result
        state["execution_success"] = True
        state["row_count"] = result["row_count"]
    except Exception as e:
        state["execution_success"] = False
        state["execution_result"] = {"error": str(e), "columns": [], "rows": [], "row_count": 0}
        state["row_count"] = 0

    return state


async def verify_node(state: SQLAgentState) -> dict:
    """verify 节点: 语义验证。"""
    if progress:
        await progress.publish_step("sql_worker", "verifying", "正在验证结果…")
    result = run_verification(state)
    state["semantic_check"] = result
    return state


def should_retry(state: SQLAgentState) -> Literal["generate", "END"]:
    """条件边: 判断是否回到 generate 重试。"""
    # Guard 失败 → 重试
    if not state.get("guard_passed", True):
        state["convergence_reason"] = "guard_failed"
        return "generate"

    # 执行失败 → 重试
    if not state.get("execution_success", False):
        state["convergence_reason"] = "execution_failed"
        return "generate"

    # 语义验证失败
    semantic = state.get("semantic_check", "")
    if semantic.startswith("failed:"):
        state["convergence_reason"] = "verify_failed"
        return "generate"

    # 检查收敛
    converged, reason = check_convergence(state)
    state["convergence_reason"] = reason
    return "END"


def build_sql_agent_graph(progress=None) -> StateGraph:
    """构建 SQL Agent 的 LangGraph StateGraph。"""
    graph = StateGraph(SQLAgentState)

    graph.add_node("generate", generate_node)
    graph.add_node("guard", guard_node)
    graph.add_node("execute", execute_node)
    graph.add_node("verify", verify_node)

    graph.set_entry_point("generate")

    graph.add_edge("generate", "guard")

    graph.add_conditional_edges(
        "guard",
        lambda s: "execute" if s.get("guard_passed", False) else "generate",
        {"execute": "execute", "generate": "generate"},
    )

    graph.add_edge("execute", "verify")

    graph.add_conditional_edges(
        "verify",
        should_retry,
        {"generate": "generate", "END": END},
    )

    return graph.compile()
