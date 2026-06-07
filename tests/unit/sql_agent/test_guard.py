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
