"""Query Rewriter 双层缓存(L1 Redis + L2 pgvector)。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §3
"""

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import asyncpg
import redis

logger = logging.getLogger(__name__)


class L1Cache:
    """Redis 精确匹配缓存。健康降级:Redis 出错时不抛异常。"""

    KEY_PREFIX = "qr:exact:"

    def __init__(self, redis_client, ttl_s: int = 3600):
        self._redis = redis_client
        self._ttl = ttl_s

    def _key(self, query_hash: str) -> str:
        return f"{self.KEY_PREFIX}{query_hash}"

    async def get(self, query_hash: str) -> dict | None:
        try:
            raw = await self._redis.get(self._key(query_hash))
        except (redis.RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "qr l1 get failed for hash=%s: %s: %s",
                query_hash,
                type(e).__name__,
                e,
            )
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("qr l1 payload not json, dropping hash=%s", query_hash)
            return None

    async def set(self, query_hash: str, payload: dict) -> None:
        try:
            await self._redis.setex(
                self._key(query_hash),
                self._ttl,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except (redis.RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "qr l1 set failed for hash=%s: %s: %s",
                query_hash,
                type(e).__name__,
                e,
            )

    async def delete(self, query_hash: str) -> None:
        try:
            await self._redis.delete(self._key(query_hash))
        except (redis.RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "qr l1 delete failed for hash=%s: %s: %s",
                query_hash,
                type(e).__name__,
                e,
            )


_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b1[3-9]\d{9}\b"),                    # 中国手机号
    re.compile(r"\b\d{17}[\dXx]\b"),                   # 中国身份证
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),       # email
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),            # 信用卡
)


def contains_pii(text: str) -> bool:
    """粗略 PII 检测:正则匹配 PII 即视为含 PII。"""
    return any(p.search(text) for p in _PII_PATTERNS)


class L2Cache:
    """pgvector 单 SQL 双召回缓存。

    lookup(): 单条 SQL 同时按精确 hash 与 cosine 距离排序,
              优先返回精确命中。
    put(): 含 PII 时直接跳过(PII 不入库,仅由 L1 短 TTL 兜底)。
    """

    def __init__(self, pg_pool, embedding_dim: int = 1024,
                 distance_threshold: float = 0.08,
                 ttl_s: int = 86400):
        self._pool = pg_pool
        self._dim = embedding_dim
        self._threshold = distance_threshold
        self._ttl_s = ttl_s

    async def lookup(
        self,
        *,
        query_hash: str,
        query_embedding: Sequence[float],
        weights_version: int,
        synonym_version: int,
    ) -> dict | None:
        sql = """
            SELECT payload,
                   CASE WHEN query_hash = $1 THEN 'exact_match'::text
                        ELSE 'semantic_match'::text END AS match_type,
                   (embedding <=> $2) AS cosine_distance
            FROM qr_cache_entries
            WHERE (query_hash = $1 OR (embedding <=> $2) < $3)
              AND weights_version = $4
              AND synonym_version = $5
              AND ttl_ts > NOW()
            ORDER BY CASE WHEN query_hash = $1 THEN 0 ELSE 1 END,
                     embedding <=> $2
            LIMIT 1
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    sql, query_hash, list(query_embedding),
                    self._threshold, weights_version, synonym_version,
                )
        except (asyncpg.PostgresError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning("qr l2 lookup failed: %s: %s", type(e).__name__, e)
            return None
        if row is None:
            return None
        return {
            "payload": row["payload"],
            "match_type": row["match_type"],
        }

    async def put(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        payload: dict,
        weights_version: int,
        synonym_version: int,
        query_hash: str,
    ) -> int | None:
        """返回新 cache_id;含 PII 时返回 None 且不写库。"""
        if contains_pii(query):
            logger.info("qr l2 put skipped due to PII: %s", query[:50])
            return None
        ttl_ts = datetime.now(UTC) + timedelta(seconds=self._ttl_s)
        sql = """
            INSERT INTO qr_cache_entries
                (query_hash, weights_version, synonym_version,
                 embedding, payload, ttl_ts, query_preview)
            VALUES ($1, $2, $3, $4::vector, $5::jsonb, $6, $7)
            ON CONFLICT (query_hash, weights_version, synonym_version) DO UPDATE
                SET payload = EXCLUDED.payload,
                    ttl_ts = EXCLUDED.ttl_ts,
                    embedding = EXCLUDED.embedding
            RETURNING cache_id
        """
        try:
            async with self._pool.acquire() as conn:
                new_id = await conn.fetchval(
                    sql, query_hash, weights_version, synonym_version,
                    list(query_embedding),
                    json.dumps(payload, ensure_ascii=False),
                    ttl_ts, query[:64],
                )
                return int(new_id) if new_id is not None else None
        except (asyncpg.PostgresError, ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as e:
            logger.warning("qr l2 put failed: %s: %s", type(e).__name__, e)
            return None
