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
