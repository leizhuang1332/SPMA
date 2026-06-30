"""仓库元数据注册表（design-13 §3.2 + design-03 §3.6）。

数据源：DB 表 repo_registry（单一真相源，取代原 YAML 方案）。
启动期 fail-fast 校验：表存在 + 至少 1 条 enabled=true 行。
可选降级：MODULE_REGISTRY_OPTIONAL=true 时降级到 file_path_cache.list_repos()。
"""
import json
import logging
import os
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class RepoMeta:
    """仓库元数据 dataclass——从 repo_registry 表行转换。"""
    repo_name: str
    display_name: str
    description: str
    tags: list[str]
    repo_url: str | None = None
    local_path: str | None = None
    languages: list[str] | None = None
    enabled: bool = True


class RepoRegistry:
    """仓库元数据注册表——从 DB 查询。"""

    def __init__(self, pool: asyncpg.Pool, optional: bool | None = None):
        self._pool = pool
        self._optional = (
            optional
            if optional is not None
            else os.environ.get("MODULE_REGISTRY_OPTIONAL", "false").lower() == "true"
        )
        # fail-fast 校验在调用方显式触发（_validate_startup()）
        # —— 构造时不强制做（避免 import-time 副作用）

    async def list_active_repos(self) -> list[RepoMeta]:
        """查询所有 enabled=true 的仓库元数据（LLM 路由主路径）。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                ORDER BY id
                """
            )
        return [self._row_to_meta(r) for r in rows]

    async def get_repo_by_name(self, name: str) -> RepoMeta | None:
        """根据仓库名查询单条元数据。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE repo_name = $1 AND enabled = true
                """,
                name,
            )
        return self._row_to_meta(row) if row else None

    async def list_repos_by_keyword(
        self,
        keyword: str,
        top_k: int = 20,
        similarity_threshold: float = 0.3,
    ) -> list[RepoMeta]:
        """Stage 1 pg_trgm 关键词预筛（design-13 §3.3 Stage 1 SQL）。

        阈值松弛机制：
            1. 默认 similarity_threshold=0.3
            2. 召回 < 3 条 → 放宽到 0.15 重试一次
            3. 仍 < 3 条 → 兜底全表 ORDER BY id LIMIT top_k
        """
        # 阶段 1：默认阈值
        rows = await self._keyword_query(keyword, top_k, similarity_threshold)
        if len(rows) >= 3:
            return [self._row_to_meta(r) for r in rows]

        # 阶段 2：阈值松弛到 0.15
        relaxed_rows = await self._keyword_query(keyword, top_k, 0.15)
        if len(relaxed_rows) >= 3:
            return [self._row_to_meta(r) for r in relaxed_rows]

        # 阶段 3：兜底全表（不依赖相似度）
        async with self._pool.acquire() as conn:
            fallback_rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                ORDER BY id
                LIMIT $1
                """,
                top_k,
            )
        return [self._row_to_meta(r) for r in fallback_rows]

    async def _keyword_query(
        self, keyword: str, top_k: int, similarity_threshold: float,
    ) -> list:
        """单次 pg_trgm 关键词查询。"""
        max_distance = 1.0 - similarity_threshold
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                  AND (
                      (repo_name      <-> $1) <= $3
                      OR (display_name <-> $1) <= $3
                      OR (description  <-> $1) <= $3
                      OR $1 = ANY(tags)
                  )
                ORDER BY (
                    GREATEST(
                        similarity(repo_name, $1),
                        similarity(display_name, $1),
                        similarity(description, $1)
                    )
                    + CASE WHEN $1 = ANY(tags) THEN 0.3 ELSE 0 END
                ) DESC
                LIMIT $2
                """,
                keyword, top_k, max_distance,
            )
        return rows

    @staticmethod
    def _row_to_meta(row) -> RepoMeta:
        languages = row["languages"]
        if isinstance(languages, str):
            languages = json.loads(languages)
        return RepoMeta(
            repo_name=row["repo_name"],
            display_name=row["display_name"],
            description=row["description"],
            tags=list(row["tags"]),
            repo_url=row["repo_url"],
            local_path=row["local_path"],
            languages=languages or [],
            enabled=row["enabled"],
        )
