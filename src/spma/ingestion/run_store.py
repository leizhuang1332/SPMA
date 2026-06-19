"""Pipeline 运行状态存储——ingestion_runs 表的 CRUD。"""

import json
import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


class PipelineRunStore:
    """管理 ingestion_runs 表的读写。"""

    def __init__(self, db_pool: asyncpg.Pool):
        self._db_pool = db_pool

    async def create(
        self,
        pipeline_type: str,
        source: str | None,
        mode: str,
        created_by: str,
    ) -> str:
        """创建一条运行记录，返回 run_id。格式: ingest-{type}-{YYYYMMDD}-{HHmmss}"""
        now = datetime.now(timezone.utc)
        run_id = f"ingest-{pipeline_type}-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"

        async with self._db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ingestion_runs
                    (pipeline_run_id, pipeline_type, source, mode, status, started_at, created_by)
                VALUES ($1, $2, $3, $4, 'running', $5, $6)
                """,
                run_id, pipeline_type, source, mode, now.isoformat(), created_by,
            )

        logger.info(f"创建 pipeline run: {run_id} (type={pipeline_type}, mode={mode})")
        return run_id

    async def update(
        self,
        run_id: str,
        status: str,
        stats: dict | None = None,
        errors: list | None = None,
        completed_at: str | None = None,
    ) -> None:
        """原子更新运行状态、统计和错误。completed_at 仅当显式传入时更新。"""

        async with self._db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_runs
                SET status = $2,
                    stats = COALESCE($3::jsonb, stats),
                    errors = COALESCE($4::jsonb, errors),
                    completed_at = COALESCE($5::timestamptz, completed_at)
                WHERE pipeline_run_id = $1
                """,
                run_id,
                status,
                json.dumps(stats or {}),
                json.dumps(errors or []),
                completed_at,
            )

    async def get(self, run_id: str) -> dict | None:
        """查询单条运行记录。"""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT pipeline_run_id, pipeline_type, source, mode, status,
                       started_at, completed_at, estimated_completion,
                       stats, errors, created_by, created_at
                FROM ingestion_runs
                WHERE pipeline_run_id = $1
                """,
                run_id,
            )
            return dict(row) if row else None

    async def get_latest(self, pipeline_type: str) -> dict | None:
        """获取指定类型的最新运行记录。"""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT pipeline_run_id, pipeline_type, source, mode, status,
                       started_at, completed_at, estimated_completion,
                       stats, errors, created_by, created_at
                FROM ingestion_runs
                WHERE pipeline_type = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                pipeline_type,
            )
            return dict(row) if row else None

    async def get_latest_successful(
        self, pipeline_type: str, source_type: str | None = None
    ) -> dict | None:
        """获取最近一次成功完成的运行记录。

        Args:
            pipeline_type: "doc" | "code" | "sql"
            source_type: 可选，进一步过滤 source（如 "markdown_dir"）。为 None 时不按 source 过滤。

        Returns:
            最近一次 status='completed' 的运行记录，无匹配时返回 None。
        """
        async with self._db_pool.acquire() as conn:
            if source_type:
                row = await conn.fetchrow(
                    """
                    SELECT pipeline_run_id, pipeline_type, source, mode, status,
                           started_at, completed_at, estimated_completion,
                           stats, errors, created_by, created_at
                    FROM ingestion_runs
                    WHERE pipeline_type = $1
                      AND source = $2
                      AND status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    pipeline_type, source_type,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT pipeline_run_id, pipeline_type, source, mode, status,
                           started_at, completed_at, estimated_completion,
                           stats, errors, created_by, created_at
                    FROM ingestion_runs
                    WHERE pipeline_type = $1
                      AND status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    pipeline_type,
                )
            return dict(row) if row else None

    async def list_recent(self, limit: int = 20) -> list[dict]:
        """获取最近的运行记录。"""
        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pipeline_run_id, pipeline_type, source, mode, status,
                       started_at, completed_at, stats, errors
                FROM ingestion_runs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]
