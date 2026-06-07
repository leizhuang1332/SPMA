# tests/integration/test_sql_agent_loop.py
"""MockLLM 下的 Agent 循环集成测试——验证三种收敛模式。"""
import pytest

from spma.agents.sql.state import SQLAgentState
from spma.agents.sql.guard import run_full_guard
from spma.agents.sql.convergence import check_convergence

MOCK_SCHEMA = {
    "orders": {"id", "status", "amount", "user_id", "created_at"},
    "users": {"id", "name", "email", "created_at"},
}


def make_state(query: str, current_round: int = 1, sql: str = "",
               row_count: int = 0, success: bool = True) -> SQLAgentState:
    import time
    return SQLAgentState(
        query=query,
        original_query=query,
        current_round=current_round,
        max_rounds=5,
        timeout_ms=3000,
        start_time=time.time(),
        sql_history=[sql] if sql else [],
        generated_sql=sql,
        row_count=row_count,
        execution_success=success,
        guard_passed=True,
    )


def test_first_round_convergence():
    """首轮收敛: 行数在正常范围。"""
    state = make_state(
        query="各状态订单数",
        sql="SELECT status, COUNT(*) FROM orders GROUP BY status",
        row_count=3,
    )
    converged, reason = check_convergence(state)
    assert converged
    assert "deterministic" in reason.lower()


def test_third_round_convergence():
    """第三轮收敛: 前两轮 SQL 语法有误，第三轮正确。"""
    state = make_state(
        query="各状态订单数",
        current_round=3,
        sql="SELECT status, COUNT(*) FROM orders GROUP BY status",
        row_count=5,
    )
    converged, _ = check_convergence(state)
    assert converged


def test_never_converges_force_stop():
    """永不收敛: 第 5 轮强制停止。"""
    state = make_state(
        query="各状态订单数",
        current_round=5,
        sql="SELECT * FROM orders",
        row_count=50000,
    )
    converged, reason = check_convergence(state)
    assert converged
    assert "max_rounds" in reason.lower()


def test_guard_rejects_delete():
    """Guard 拦截 DELETE。"""
    result = run_full_guard("DELETE FROM orders WHERE id = 1", MOCK_SCHEMA)
    assert not result["passed"]
    assert "DELETE" in result["forbidden_operations"]


def test_guard_rejects_nonexistent_table():
    """Guard 检测不存在的表。"""
    result = run_full_guard("SELECT * FROM nonexistent_table", MOCK_SCHEMA)
    assert not result["passed"]
    assert len(result["table_existence_errors"]) > 0


def test_guard_passes_valid_query():
    """Guard 通过合法查询。"""
    result = run_full_guard(
        "SELECT status, COUNT(*) FROM orders WHERE created_at > '2026-01-01' GROUP BY status",
        MOCK_SCHEMA,
    )
    assert result["passed"]
