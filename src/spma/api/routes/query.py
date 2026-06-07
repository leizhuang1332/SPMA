"""查询端点——POST /api/v1/query + /api/v1/sql/query。

设计依据: API-01 §2 核心端点
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SqlQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    auto_confirm: bool = False


@router.post("/api/v1/sql/query")
async def sql_query(req: SqlQueryRequest):
    """SQL Agent 查询端点——自然语言 → SQL 执行。

    Slice 1 Mock: 使用硬编码 Schema + SQLite 内存库。
    """
    from spma.agents.sql.state import SQLAgentState
    from spma.agents.sql.graph import (
        set_schema_snapshot,
        generate_node,
        guard_node,
        execute_node,
        verify_node,
    )
    from spma.agents.sql.convergence import check_convergence

    # Mock Schema 快照
    set_schema_snapshot({
        "orders": {"id", "status", "amount", "user_id", "created_at"},
        "users": {"id", "name", "email", "created_at"},
        "products": {"id", "name", "price", "category"},
    })

    # 初始化 Mock 执行器
    from spma.agents.sql.executor import MockExecutor
    schema_sql = """
        CREATE TABLE orders (id INTEGER, status TEXT, amount REAL, user_id INTEGER, created_at TEXT);
        CREATE TABLE users (id INTEGER, name TEXT, email TEXT, created_at TEXT);
        CREATE TABLE products (id INTEGER, name TEXT, price REAL, category TEXT);
    """
    sample_data = {
        "orders": [
            (1, "paid", 100.0, 1, "2026-06-01"),
            (2, "pending", 50.0, 2, "2026-06-02"),
            (3, "paid", 200.0, 1, "2026-06-03"),
            (4, "cancelled", 30.0, 2, "2026-06-04"),
        ],
        "users": [
            (1, "Alice", "alice@example.com", "2026-01-01"),
            (2, "Bob", "bob@example.com", "2026-02-01"),
        ],
        "products": [
            (1, "Widget", 50.0, "widgets"),
            (2, "Gadget", 100.0, "gadgets"),
        ],
    }
    executor = MockExecutor(schema_sql, sample_data)

    # 构建初始状态
    import time
    state = SQLAgentState(
        query=req.query,
        original_query=req.query,
        current_round=0,
        max_rounds=5,
        timeout_ms=3000,
        sql_history=[],
        start_time=time.time(),
        _executor=executor,
    )

    # 运行 Agent 循环
    for _ in range(state["max_rounds"]):
        # generate
        await generate_node(state)

        # guard
        guard_node(state)
        guard_result = state.get("guard_result")
        if guard_result and not guard_result.get("passed", True):
            return {
                "status": "blocked",
                "guard_result": {
                    "passed": False,
                    "forbidden_operations": guard_result.get("forbidden_operations", []),
                    "syntax_errors": guard_result.get("syntax_errors", []),
                    "table_existence_errors": guard_result.get("table_existence_errors", []),
                    "risk_level": guard_result.get("risk_level", "blocked"),
                },
            }

        # execute
        await execute_node(state)

        # verify
        verify_node(state)

        # 检查是否收敛
        converged, reason = check_convergence(state)
        if converged:
            break

    execution_result = state.get("execution_result", {})

    return {
        "status": "completed",
        "sql": state.get("generated_sql", ""),
        "result": {
            "columns": execution_result.get("columns", []),
            "rows": execution_result.get("rows", []),
            "row_count": execution_result.get("row_count", 0),
            "execution_time_ms": execution_result.get("execution_time_ms", 0),
            "replica_lag_ms": execution_result.get("replica_lag_ms", 0),
            "data_snapshot_at": execution_result.get("data_snapshot_at", ""),
        },
        "rounds": state.get("current_round", 1),
        "quality_report": {
            "issues": [],
            "confidence": 1.0,
        },
    }
