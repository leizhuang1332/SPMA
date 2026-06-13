"""文件路径缓存——git ls-files -> PostgreSQL 缓存。"""

import asyncio
import logging

logger = logging.getLogger(__name__)

FILE_TYPE_MAP = {
    ".py": "python", ".java": "java", ".go": "go", ".ts": "typescript",
    ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".rs": "rust", ".rb": "ruby", ".php": "php",
}


class FilePathCache:
    """管理代码仓库的文件路径缓存（PostgreSQL 后端）。"""

    def __init__(self, db_pool):
        self._db_pool = db_pool

    async def build_cache(self, repo_name: str, repo_path: str) -> int:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "ls-files",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        files = stdout.decode("utf-8", errors="replace").strip().split("\n")
        count = 0
        async with self._db_pool.acquire() as conn:
            await conn.execute("DELETE FROM file_path_cache WHERE repo_name = $1", repo_name)
            for file_path in files:
                if not file_path.strip():
                    continue
                ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
                file_type = FILE_TYPE_MAP.get(ext, "other")
                await conn.execute(
                    """INSERT INTO file_path_cache (repo_name, file_path, file_type)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (repo_name, file_path) DO UPDATE
                       SET file_type = $3, updated_at = NOW()""",
                    repo_name, file_path, file_type,
                )
                count += 1
        logger.info(f"file_path_cache 构建完成: {repo_name} -> {count} files")
        return count

    async def query_files(self, keyword: str, limit: int = 10) -> list[dict]:
        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT repo_name, file_path, file_type FROM file_path_cache
                   WHERE file_path ILIKE $1
                   ORDER BY similarity(file_path, $2) DESC LIMIT $3""",
                f"%{keyword}%", keyword, limit,
            )
            return [dict(r) for r in rows]

    async def incremental_update(self, repo_name: str, changed_files: list[str]) -> int:
        count = 0
        async with self._db_pool.acquire() as conn:
            for file_path in changed_files:
                ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
                file_type = FILE_TYPE_MAP.get(ext, "other")
                await conn.execute(
                    """INSERT INTO file_path_cache (repo_name, file_path, file_type)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (repo_name, file_path) DO UPDATE
                       SET file_type = $3, updated_at = NOW()""",
                    repo_name, file_path, file_type,
                )
                count += 1
        return count

    async def list_repos(self) -> list[str]:
        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT DISTINCT repo_name FROM file_path_cache LIMIT 50")
            return [r["repo_name"] for r in rows]
