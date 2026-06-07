"""数据质量检测——执行后结果质量扫描。

检查: 空结果、NULL比例异常、数值列异常值、数据新鲜度

原则: 检测不阻塞，标注不隐藏。

设计依据: SPMA-design-04 §4.2 数据质量问题
"""

from datetime import datetime, timezone

from spma.agents.sql.state import QualityReport, QualityIssue


def generate_quality_report(
    columns: list[str],
    rows: list[list],
    row_count: int,
    sql: str,
    replica_lag_ms: int = 0,
    data_snapshot_at: str = "",
) -> QualityReport:
    """对执行结果做基本统计质量扫描，生成 QualityReport。"""
    issues: list[QualityIssue] = []

    # 1. 空结果检测
    if row_count == 0:
        sql_lower = sql.lower()
        cause = "过滤条件过严或时间范围无数据"
        if "where" not in sql_lower:
            cause = "表名可能选错或无 WHERE 条件的全表扫描"
        issues.append(QualityIssue(
            type="empty_result",
            severity="warning",
            column=None,
            detail=f"返回 0 行。可能原因: {cause}",
        ))

    # 2. NULL 比例异常
    if columns and rows:
        col_count = len(columns)
        for ci in range(col_count):
            null_count = sum(1 for row in rows if ci >= len(row) or row[ci] is None)
            if row_count > 0 and null_count / row_count > 0.5:
                issues.append(QualityIssue(
                    type="null_anomaly",
                    severity="warning",
                    column=columns[ci],
                    detail=f"列 `{columns[ci]}` NULL 占比 {null_count}/{row_count} ({null_count/row_count:.0%})，聚合计算已自动排除 NULL 值",
                ))

    # 3. 数值列异常值检测
    if columns and rows and row_count >= 3:
        for ci in range(col_count):
            values = []
            for row in rows:
                if ci < len(row) and row[ci] is not None:
                    try:
                        values.append(float(row[ci]))
                    except (ValueError, TypeError):
                        break
            if len(values) >= 3:
                values.sort()
                p99_index = max(0, int(len(values) * 0.99) - 1)
                p99 = values[p99_index] if p99_index < len(values) else values[-1]
                max_val = max(values)
                if max_val > p99 * 10 and p99 > 0:
                    issues.append(QualityIssue(
                        type="outlier",
                        severity="warning",
                        column=columns[ci],
                        detail=f"列 `{columns[ci]}` 存在极端值（最大 {max_val} vs 99分位 {p99}），平均值可能失真",
                    ))

    # 4. 数据新鲜度
    if replica_lag_ms > 5000:
        issues.append(QualityIssue(
            type="stale_data",
            severity="info",
            column=None,
            detail=f"数据存在约 {replica_lag_ms // 1000} 秒延迟，截止 {data_snapshot_at}，不包含此后数据",
        ))

    confidence = max(0.0, 1.0 - (len(issues) * 0.2))
    return QualityReport(
        issues=issues,
        issue_count=len(issues),
        confidence=confidence,
        data_snapshot_at=data_snapshot_at or datetime.now(timezone.utc).isoformat(),
        replica_lag_ms=replica_lag_ms,
    )
