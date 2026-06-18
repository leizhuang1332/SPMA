"""同义词映射表管理——用户用语 → 系统内部名的标准化映射。

冷启动数据来源: information_schema + PRD标题 + Git目录 + 人工补充
持续维护: 自动发现 + 人工审核 + 衰变检查

设计依据: SPMA-design-01 §8.2 映射表维护 + SPMA-design-05 数据摄入管道设计
"""

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)


class SynonymMap:
    """同义词映射表 CRUD + 刷新。"""

    def __init__(self, db_pool: asyncpg.Pool, config: dict | None = None):
        self._db_pool = db_pool
        self._config = config or {}

    async def refresh(
        self,
        sources: list[str],
        auto_apply_threshold: float = 0.9,
    ) -> int:
        """从多个数据源扫描新映射。

        Args:
            sources: ["information_schema", "prd_titles", "git_dirs"]
            auto_apply_threshold: 高于此置信度的自动激活

        Returns:
            新增的映射条目数
        """
        added = 0

        for source in sources:
            if source == "information_schema":
                added += await self._extract_from_information_schema(auto_apply_threshold)
            elif source == "prd_titles":
                added += await self._extract_from_prd_titles(auto_apply_threshold)
            elif source == "git_dirs":
                added += await self._extract_from_git_dirs(auto_apply_threshold)

        logger.info(f"同义词映射刷新完成: 新增 {added} 条")
        return added

    async def query(self, status: str = "all", limit: int = 100) -> dict:
        """分页/过滤查询映射表。

        Returns:
            {"total": int, "entries": [dict]}
        """
        async with self._db_pool.acquire() as conn:
            if status == "all":
                rows = await conn.fetch(
                    """
                    SELECT id, user_term, canonical_term, category, source,
                           confidence, status, hits_30d, last_triggered_at, created_at
                    FROM synonym_map
                    ORDER BY hits_30d DESC, confidence DESC
                    LIMIT $1
                    """,
                    limit,
                )
                count_row = await conn.fetchrow("SELECT COUNT(*) AS count FROM synonym_map")
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, user_term, canonical_term, category, source,
                           confidence, status, hits_30d, last_triggered_at, created_at
                    FROM synonym_map
                    WHERE status = $1
                    ORDER BY hits_30d DESC, confidence DESC
                    LIMIT $2
                    """,
                    status, limit,
                )
                count_row = await conn.fetchrow(
                    "SELECT COUNT(*) AS count FROM synonym_map WHERE status = $1", status
                )

            entries = [dict(r) for r in rows]
            return {
                "total": count_row["count"] if count_row else 0,
                "entries": entries,
            }

    async def lookup(self, user_term: str) -> str | None:
        """单条查询——返回 canonical_term 并更新命中计数。"""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT canonical_term FROM synonym_map
                WHERE user_term = $1 AND status = 'active'
                """,
                user_term,
            )
            if row:
                # 更新命中计数
                await conn.execute(
                    """
                    UPDATE synonym_map
                    SET hits_30d = hits_30d + 1,
                        last_triggered_at = NOW()
                    WHERE user_term = $1
                    """,
                    user_term,
                )
                return row["canonical_term"]
            return None

    async def apply_entry(self, entry_id: int) -> None:
        """激活 pending_review 条目。"""
        async with self._db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE synonym_map SET status = 'active', updated_at = NOW() WHERE id = $1",
                entry_id,
            )

    async def mark_deprecated(self, entry_id: int) -> None:
        """标记条目为 deprecated。"""
        async with self._db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE synonym_map SET status = 'deprecated', updated_at = NOW() WHERE id = $1",
                entry_id,
            )

    async def _extract_from_information_schema(self, threshold: float) -> int:
        """从 information_schema 提取表名↔表注释映射。"""
        added = 0
        async with self._db_pool.acquire() as conn:
            try:
                rows = await conn.fetch("""
                    SELECT table_name,
                           pg_catalog.obj_description(c.oid, 'pg_class') AS table_comment
                    FROM information_schema.tables t
                    JOIN pg_catalog.pg_class c ON c.relname = t.table_name
                    WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                """)
                for row in rows:
                    comment = row.get("table_comment")
                    if comment:
                        status = "active" if threshold <= 0.9 else "pending_review"
                        await conn.execute(
                            """
                            INSERT INTO synonym_map (user_term, canonical_term, category, source, confidence, status)
                            VALUES ($1, $2, 'table_name', 'information_schema', 0.95, $3)
                            ON CONFLICT DO NOTHING
                            """,
                            comment, row["table_name"], status,
                        )
                        added += 1
            except Exception as e:
                logger.warning(f"information_schema 映射提取失败: {e}")
        return added

    async def _extract_from_prd_titles(self, threshold: float) -> int:
        """从 PRD 文档标题提取关键词映射。

        遍历 ES 中已索引文档的 page_title 字段，提取 REQ-XXXX 关键词 → 标题 映射。
        """
        import re

        try:
            from spma.retrieval.es_client import ESClient
            es = ESClient()
            # 搜索所有有 page_title 的文档
            results = await es.search(query="*", top_k=1000)
        except Exception as e:
            logger.warning(f"无法连接 ES 提取 PRD 标题映射: {e}")
            return 0

        added = 0
        req_pattern = re.compile(r'REQ-\d{3,5}', re.IGNORECASE)
        async with self._db_pool.acquire() as conn:
            for doc in results:
                title = doc.get("page_title", "")
                if not title:
                    continue
                req_ids = req_pattern.findall(title)
                for req_id in req_ids:
                    status = "active" if threshold <= 0.9 else "pending_review"
                    await conn.execute(
                        """
                        INSERT INTO synonym_map (user_term, canonical_term, category, source, confidence, status)
                        VALUES ($1, $2, 'module', 'prd_titles', 0.85, $3)
                        ON CONFLICT DO NOTHING
                        """,
                        req_id.upper(), title, status,
                    )
                    added += 1
        return added

    async def _extract_from_git_dirs(self, threshold: float) -> int:
        """从 Git 仓库目录结构提取模块名映射。
        注: 需要遍历 file_path_cache 表。
        """
        async with self._db_pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    "SELECT DISTINCT repo_name FROM file_path_cache"
                )
                added = 0
                for row in rows:
                    repo_name = row["repo_name"]
                    status = "active" if threshold <= 0.9 else "pending_review"
                    await conn.execute(
                        """
                        INSERT INTO synonym_map (user_term, canonical_term, category, source, confidence, status)
                        VALUES ($1, $2, 'module', 'git_dirs', 0.9, $3)
                        ON CONFLICT DO NOTHING
                        """,
                        repo_name.replace("-", " ").title(), repo_name, status,
                    )
                    added += 1
                return added
            except Exception as e:
                logger.warning(f"git_dirs 映射提取失败: {e}")
                return 0
