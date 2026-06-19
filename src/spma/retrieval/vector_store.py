"""PGVector 向量存储客户端。

表结构对齐 LlamaIndex PGVectorStore 标准: data_chunk_embeddings
列: id (BIGINT PK), text (VARCHAR), metadata_ (JSONB), node_id (VARCHAR),
     embedding (VECTOR(1024)), text_search_tsv (TSVECTOR computed)

索引: HNSW (m=16, ef_construction=200, ef_search=100)
距离: cosine
能力: 向量检索 + 元数据过滤
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
        table: str = "data_chunk_embeddings",
    ) -> list[dict]:
        """向量相似度搜索（cosine 距离）。

        Args:
            embedding: 查询向量，1024 维
            top_k: 返回数量
            table: 表名，默认 data_chunk_embeddings

        Returns:
            [{node_id, text, score, metadata_}, ...]
            其中 metadata_ 内含 source_id, source_type 等业务字段
        """
        pool = await self._ensure_pool()
        vector_str = f"[{','.join(str(v) for v in embedding)}]"

        # 使用余弦距离排序（按相似度降序 → 距离升序）
        rows = await pool.fetch(
            f"""
            SELECT node_id, text,
                   1 - (embedding <=> $1::vector) AS score,
                   metadata_
            FROM {table}
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vector_str,
            top_k,
        )

        return [
            {
                "node_id": row["node_id"],
                "text": row["text"],
                "score": float(row["score"]),
                "metadata_": row["metadata_"] or {},
            }
            for row in rows
        ]

    async def upsert(
        self,
        node_id: str,
        text: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        """插入或更新向量记录。

        由于 LlamaIndex data_chunk_embeddings 表 node_id 无 UNIQUE 约束，
        先删后插实现 upsert 语义。
        """
        import json

        pool = await self._ensure_pool()
        vector_str = f"[{','.join(str(v) for v in embedding)}]"
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 先删除同 node_id 的旧记录
                await conn.execute(
                    "DELETE FROM data_chunk_embeddings WHERE node_id = $1",
                    node_id,
                )
                # 再插入新记录
                await conn.execute(
                    """
                    INSERT INTO data_chunk_embeddings (node_id, text, embedding, metadata_)
                    VALUES ($1, $2, $3::vector, $4)
                    """,
                    node_id,
                    text,
                    vector_str,
                    json.dumps(metadata or {}),
                )

    async def upsert_batch(
        self,
        chunks: list[dict],
    ) -> None:
        """批量插入或更新向量记录。

        Args:
            chunks: 列表，每个元素为 {
                node_id, text,
                embedding (list[float]), metadata (dict | None)
            }
        """
        import json

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for chunk in chunks:
                    vector_str = f"[{','.join(str(v) for v in chunk['embedding'])}]"
                    node_id = chunk["node_id"]
                    # 先删除同 node_id 的旧记录
                    await conn.execute(
                        "DELETE FROM data_chunk_embeddings WHERE node_id = $1",
                        node_id,
                    )
                    # 再插入新记录
                    await conn.execute(
                        """
                        INSERT INTO data_chunk_embeddings (node_id, text, embedding, metadata_)
                        VALUES ($1, $2, $3::vector, $4)
                        """,
                        node_id,
                        chunk["text"],
                        vector_str,
                        json.dumps(chunk.get("metadata") or {}),
                    )

    async def delete_by_source(self, source_id: str) -> int:
        """按 source_id 删除所有关联向量记录。

        source_id 现在保存在 metadata_ JSONB 中，通过 -> 操作符查询。

        Returns:
            删除的记录数
        """
        pool = await self._ensure_pool()
        rows = await pool.fetch(
            "DELETE FROM data_chunk_embeddings WHERE metadata_->>'source_id' = $1 RETURNING node_id",
            source_id,
        )
        return len(rows)

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
