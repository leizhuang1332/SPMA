"""PGVector 向量存储客户端。

索引: HNSW (m=16, ef_construction=200, ef_search=100)
距离: cosine
能力: 向量检索 + SQL JOIN 元数据过滤（同一事务内）
"""

import logging
from typing import Any

import asyncpg

from spma.infrastructure.circuit_breaker import circuit_breaker

logger = logging.getLogger(__name__)


class PGVectorStore:
    """PGVector 异步向量存储客户端。

    使用 asyncpg 直连 PostgreSQL + pgvector 扩展。
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or "postgresql://spma:spma123@localhost:5433/spma"
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=2,
                max_size=10,
            )
            # 设置 ef_search（匹配 config/spma.yaml）
            async with self._pool.acquire() as conn:
                await conn.execute("SET hnsw.ef_search = 100")
        return self._pool

    @circuit_breaker("pgvector")
    async def search(
        self,
        embedding: list[float],
        top_k: int = 20,
        table: str = "chunk_embeddings",
    ) -> list[dict]:
        """向量相似度搜索（cosine 距离）。

        Args:
            embedding: 查询向量，1024 维
            top_k: 返回数量
            table: 表名，默认 chunk_embeddings

        Returns:
            [{chunk_id, source_id, source_type, content, score, ...}, ...]
        """
        pool = await self._ensure_pool()
        vector_str = f"[{','.join(str(v) for v in embedding)}]"

        # 使用余弦距离排序（按相似度降序 → 距离升序）
        rows = await pool.fetch(
            f"""
            SELECT chunk_id, source_id, source_type, content,
                   1 - (embedding <=> $1::vector) AS score,
                   metadata
            FROM {table}
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vector_str,
            top_k,
        )

        return [
            {
                "chunk_id": row["chunk_id"],
                "source_id": row["source_id"],
                "source_type": row["source_type"],
                "content": row["content"],
                "score": float(row["score"]),
                "metadata": row["metadata"] or {},
            }
            for row in rows
        ]

    async def upsert(
        self,
        chunk_id: str,
        source_id: str | None,
        source_type: str | None,
        content: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        """插入或更新向量记录。"""
        import json

        pool = await self._ensure_pool()
        vector_str = f"[{','.join(str(v) for v in embedding)}]"
        await pool.execute(
            """
            INSERT INTO chunk_embeddings (chunk_id, source_id, source_type, content, embedding, metadata)
            VALUES ($1, $2, $3, $4, $5::vector, $6)
            ON CONFLICT (chunk_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata
            """,
            chunk_id,
            source_id,
            source_type,
            content,
            vector_str,
            json.dumps(metadata or {}),
        )

    async def delete_by_source(self, source_id: str) -> int:
        """按 source_id 删除所有关联向量记录。

        Returns:
            删除的记录数
        """
        pool = await self._ensure_pool()
        result = await pool.execute(
            "DELETE FROM chunk_embeddings WHERE source_id = $1",
            source_id,
        )
        # asyncpg execute 返回 "DELETE N" 格式字符串
        deleted = int(result.split()[-1]) if result else 0
        return deleted

    async def health_check(self) -> bool:
        """检查 PGVector 连接是否可用。"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning(f"PGVector health check 失败: {e}")
            return False

    async def close(self) -> None:
        """关闭连接池。"""
        if self._pool:
            await self._pool.close()
            self._pool = None
