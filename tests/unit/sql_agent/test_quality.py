# tests/unit/sql_agent/test_quality.py
import pytest
from spma.agents.sql.quality import generate_quality_report


def test_empty_result_detection():
    report = generate_quality_report(
        columns=["status", "cnt"],
        rows=[],
        row_count=0,
        sql="SELECT status, COUNT(*) FROM orders WHERE 1=0",
        replica_lag_ms=0,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    assert report["issue_count"] >= 1
    assert any(i["type"] == "empty_result" for i in report["issues"])


def test_null_anomaly_detection():
    report = generate_quality_report(
        columns=["name", "email"],
        rows=[["Alice", None], ["Bob", None], ["Charlie", "c@x.com"]],
        row_count=3,
        sql="SELECT name, email FROM users",
        replica_lag_ms=0,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    null_issues = [i for i in report["issues"] if i["type"] == "null_anomaly"]
    assert len(null_issues) > 0


def test_outlier_detection():
    report = generate_quality_report(
        columns=["id", "amount"],
        rows=[[1, 100.0], [2, 120.0], [3, 999999.0]],
        row_count=3,
        sql="SELECT id, amount FROM orders",
        replica_lag_ms=0,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    outlier_issues = [i for i in report["issues"] if i["type"] == "outlier"]
    assert len(outlier_issues) > 0


def test_stale_data_detection():
    report = generate_quality_report(
        columns=["status"],
        rows=[["paid"]],
        row_count=1,
        sql="SELECT status FROM orders",
        replica_lag_ms=8000,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    stale_issues = [i for i in report["issues"] if i["type"] == "stale_data"]
    assert len(stale_issues) > 0


def test_confidence_calculation():
    report = generate_quality_report(
        columns=["status"],
        rows=[],
        row_count=0,
        sql="SELECT status FROM orders",
        replica_lag_ms=8000,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    assert report["confidence"] == pytest.approx(0.6)


def test_clean_result():
    report = generate_quality_report(
        columns=["status", "cnt"],
        rows=[["paid", 847], ["pending", 123]],
        row_count=2,
        sql="SELECT status, COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY status",
        replica_lag_ms=100,
        data_snapshot_at="2026-06-07T00:00:00Z",
    )
    assert report["issue_count"] == 0
    assert report["confidence"] == 1.0
