# tests/unit/sql_agent/test_guard.py
import pytest
from spma.agents.sql.guard import validate_syntax


def test_l1_rejects_chinese_punctuation():
    result = validate_syntax("SELECT status，COUNT(*) FROM orders GROUP BY status")
    assert not result["passed"]
    assert len(result["syntax_errors"]) > 0


def test_l1_rejects_missing_keyword():
    result = validate_syntax("SELECT * FROM")
    assert not result["passed"]
    assert len(result["syntax_errors"]) > 0


def test_l1_accepts_valid_sql():
    result = validate_syntax("SELECT status, COUNT(*) FROM orders GROUP BY status")
    assert result["passed"]
    assert len(result["syntax_errors"]) == 0


from spma.agents.sql.guard import validate_no_ddl_dml


def test_l2_rejects_delete():
    result = validate_no_ddl_dml("DELETE FROM orders WHERE id = 1")
    assert not result["passed"]
    assert "DELETE" in result["forbidden_operations"]


def test_l2_rejects_update():
    result = validate_no_ddl_dml("UPDATE orders SET status = 'done'")
    assert not result["passed"]
    assert "UPDATE" in result["forbidden_operations"]


def test_l2_rejects_drop():
    result = validate_no_ddl_dml("DROP TABLE orders")
    assert not result["passed"]
    assert "DROP" in result["forbidden_operations"]


def test_l2_rejects_insert():
    result = validate_no_ddl_dml("INSERT INTO orders VALUES (1, 'test')")
    assert not result["passed"]
    assert "INSERT" in result["forbidden_operations"]


def test_l2_accepts_select():
    result = validate_no_ddl_dml("SELECT * FROM orders")
    assert result["passed"]
    assert len(result["forbidden_operations"]) == 0


# ============================================================
# L3: 表/列存在性验证
# ============================================================

from spma.agents.sql.guard import validate_table_column_existence


SCHEMA_SNAPSHOT = {
    "orders": {"status", "created_at", "amount", "user_id", "id"},
    "users": {"id", "name", "email", "created_at"},
    "products": {"id", "name", "price", "category"},
}


def test_l3_rejects_nonexistent_table():
    result = validate_table_column_existence(
        "SELECT * FROM orderz",
        SCHEMA_SNAPSHOT,
    )
    assert not result["passed"]
    assert any("orderz" in e for e in result["table_existence_errors"])


def test_l3_rejects_nonexistent_column():
    result = validate_table_column_existence(
        "SELECT nonexistent_col FROM orders",
        SCHEMA_SNAPSHOT,
    )
    assert not result["passed"]
    assert any("nonexistent_col" in e for e in result["table_existence_errors"])


def test_l3_suggests_correction_for_typo():
    result = validate_table_column_existence(
        "SELECT * FROM oder_items",
        {"order_items": {"id", "name"}},
    )
    assert any("order_items" in e for e in result["table_existence_errors"])


def test_l3_accepts_valid_tables_and_columns():
    result = validate_table_column_existence(
        "SELECT status, created_at FROM orders",
        SCHEMA_SNAPSHOT,
    )
    assert result["passed"]
    assert len(result["table_existence_errors"]) == 0


# ============================================================
# L4: 性能保护
# ============================================================

from spma.agents.sql.guard import check_performance


def test_l4_warns_select_star_no_where():
    result = check_performance("SELECT * FROM orders")
    assert len(result["performance_warnings"]) > 0


def test_l4_warns_many_joins():
    result = check_performance(
        "SELECT * FROM orders o JOIN users u ON o.user_id = u.id "
        "JOIN products p ON o.product_id = p.id "
        "JOIN categories c ON p.category_id = c.id"
    )
    assert len(result["performance_warnings"]) > 0
    assert any("JOIN" in w for w in result["performance_warnings"])


def test_l4_no_warning_for_safe_query():
    result = check_performance(
        "SELECT status, COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY status"
    )
    assert not any("全表扫描" in w for w in result["performance_warnings"])


# ============================================================
# run_full_guard 编排
# ============================================================

from spma.agents.sql.guard import run_full_guard


def test_full_guard_passes_valid_query():
    result = run_full_guard(
        "SELECT status, COUNT(*) FROM orders WHERE created_at > NOW() GROUP BY status",
        SCHEMA_SNAPSHOT,
    )
    assert result["passed"]
    assert result["risk_level"] == "low"


def test_full_guard_short_circuits_on_l1():
    result = run_full_guard(
        "SELECT * FROM",
        SCHEMA_SNAPSHOT,
    )
    assert not result["passed"]
    assert len(result["syntax_errors"]) > 0


def test_full_guard_short_circuits_on_l2():
    result = run_full_guard(
        "DELETE FROM orders WHERE id = 1",
        SCHEMA_SNAPSHOT,
    )
    assert not result["passed"]
    assert "DELETE" in result["forbidden_operations"]


def test_full_guard_blocks_on_l3():
    result = run_full_guard(
        "SELECT * FROM orderz",
        SCHEMA_SNAPSHOT,
    )
    assert not result["passed"]
    assert any("orderz" in e for e in result["table_existence_errors"])


def test_full_guard_merges_l4_warnings():
    result = run_full_guard(
        "SELECT * FROM orders",
        SCHEMA_SNAPSHOT,
    )
    # 如果 L1-L3 通过，L4 的警告会被合并进最终结果
    # 没有 WHERE 且 SELECT * 会触发 L4 性能警告
    assert len(result["performance_warnings"]) > 0
