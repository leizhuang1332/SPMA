"""只读副本 SQL 执行器——连接池 + 超时控制 + 数据新鲜度记录。

Slice 1: 使用 SQLite 内存库作为 Mock。
Slice 2: 替换为 PostgreSQL 只读副本。

永远不在主库上执行。

设计依据: SPMA-design-04 §1 只读副本执行
"""

import sqlite3
import time
from datetime import datetime, timezone

from spma.agents.sql.state import QueryResult


class MockExecutor:
    """SQLite 内存库执行器——Slice 1 Mock。"""

    def __init__(self, schema_sql: str, sample_data: dict[str, list[tuple]] | None = None):
        """初始化 SQLite 内存库。

        Args:
            schema_sql: CREATE TABLE 语句，分号分隔
            sample_data: {"table_name": [(col1, col2, ...), ...]}
        """
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA query_only = OFF")  # Mock 允许写，仅用于建表
        # 执行 schema
        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.conn.execute(stmt)
        # 插入数据
        if sample_data:
            for table_name, rows in sample_data.items():
                if rows:
                    placeholders = ",".join(["?" for _ in rows[0]])
                    for row in rows:
                        self.conn.execute(
                            f"INSERT INTO {table_name} VALUES ({placeholders})",
                            row,
                        )
        self.conn.commit()

    def execute(self, sql: str, timeout_ms: int = 2000) -> QueryResult:
        """执行 SQL 查询，返回结构化结果。"""
        start = time.time()
        try:
            cursor = self.conn.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [list(row) for row in cursor.fetchall()]
            elapsed_ms = int((time.time() - start) * 1000)

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
                replica_lag_ms=0,
                data_snapshot_at=datetime.now(timezone.utc).isoformat(),
                sql_executed=sql,
            )
        except Exception as e:
            raise RuntimeError(f"SQL 执行异常: {str(e)}")
