"""知识新鲜度监控——检查三种数据源是否在 SLO 内。

数据来源: ingestion_runs 表 (最近成功时间) + chunk_embeddings/file_path_cache (最旧记录)
SLO 阈值: config/ingestion.yaml → freshness_slo

设计依据: SPMA-design-05 数据摄入管道设计 §6 新鲜度监控 + API-05 §7 新鲜度端点
"""

import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


class FreshnessService:
    """知识新鲜度查询服务。"""

    def __init__(self, db_pool: asyncpg.Pool, slo_config: dict | None = None):
        self._db_pool = db_pool
        self._slo = slo_config or {
            "doc_incremental_minutes": 5,
            "code_incremental_minutes": 5,
            "sql_polling_minutes": 10,
        }

    async def get_freshness(self) -> dict:
        """查询全量新鲜度状态（API-05 §7 响应格式）。

        Returns:
            {freshness: {documents: {...}, code: {...}, sql_schema: {...}, synonym_map: {...}}}
        """
        now = datetime.now(timezone.utc)

        doc_freshness = await self._get_pipeline_freshness("doc", now, self._slo["doc_incremental_minutes"])
        code_freshness = await self._get_pipeline_freshness("code", now, self._slo["code_incremental_minutes"])
        sql_freshness = await self._get_pipeline_freshness("sql", now, self._slo["sql_polling_minutes"])

        # Synonym map 新鲜度
        synonym_freshness = await self._get_synonym_freshness()

        return {
            "freshness": {
                "documents": doc_freshness,
                "code": code_freshness,
                "sql_schema": sql_freshness,
                "synonym_map": synonym_freshness,
            }
        }

    async def _get_pipeline_freshness(
        self,
        pipeline_type: str,
        now: datetime,
        slo_minutes: int,
    ) -> dict:
        """查询单个 pipeline 的新鲜度。"""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT completed_at, stats
                FROM ingestion_runs
                WHERE pipeline_type = $1 AND status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                pipeline_type,
            )

            most_recent = None
            within_slo = True

            if row:
                completed = row["completed_at"]
                if isinstance(completed, str):
                    completed = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                most_recent = completed.isoformat() if completed else None
                if completed:
                    delta = (now - completed).total_seconds() / 60
                    within_slo = delta <= slo_minutes
            else:
                # 没有成功运行记录 → overdue
                within_slo = False

            return {
                "most_recent_update": most_recent,
                "oldest_unindexed_change": None,
                "within_slo": within_slo,
                "slo_minutes": slo_minutes,
            }

    async def _get_synonym_freshness(self) -> dict:
        """查询同义词映射表新鲜度。"""
        async with self._db_pool.acquire() as conn:
            # total entries
            count_row = await conn.fetchrow(
                "SELECT COUNT(*) AS count FROM synonym_map WHERE status = 'active'"
            )
            total = count_row["count"] if count_row else 0

            # pending review
            pending_row = await conn.fetchrow(
                "SELECT COUNT(*) AS count FROM synonym_map WHERE status = 'pending_review'"
            )
            pending = pending_row["count"] if pending_row else 0

            # last updated
            last_row = await conn.fetchrow(
                "SELECT updated_at FROM synonym_map ORDER BY updated_at DESC LIMIT 1"
            )

            return {
                "total_entries": total,
                "last_updated": last_row["updated_at"].isoformat() if last_row and last_row["updated_at"] else None,
                "pending_review": pending,
            }

    async def check_slo(self) -> list[str]:
        """检查所有 SLO，返回超标的 pipeline 列表。"""
        freshness = await self.get_freshness()
        alerts = []
        for key, info in freshness["freshness"].items():
            if key == "synonym_map":
                continue
            if not info.get("within_slo"):
                alerts.append(key)
        if alerts:
            logger.warning(f"SLO 超标: {alerts}")
        return alerts
