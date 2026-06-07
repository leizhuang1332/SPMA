# tests/unit/sql_agent/test_confirmation.py
import pytest
from spma.agents.sql.confirmation import evaluate_confirmation_risk


def test_flags_financial_metric():
    result = evaluate_confirmation_risk("SELECT SUM(amount) FROM orders")
    assert result["requires_user_confirmation"]
    assert any("财务" in r for r in result["reasons"])


def test_flags_many_joins():
    result = evaluate_confirmation_risk(
        "SELECT * FROM orders o JOIN users u ON o.user_id = u.id "
        "JOIN products p ON o.product_id = p.id "
        "JOIN categories c ON p.category_id = c.id"
    )
    assert result["requires_user_confirmation"]


def test_flags_full_table_scan():
    result = evaluate_confirmation_risk("SELECT COUNT(*) FROM orders")
    assert result["requires_user_confirmation"]


def test_no_flag_safe_query():
    result = evaluate_confirmation_risk(
        "SELECT status, COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY status"
    )
    assert not result["requires_user_confirmation"]
