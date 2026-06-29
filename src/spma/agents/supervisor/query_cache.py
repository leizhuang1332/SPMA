"""Query Rewriter 双层缓存(L1 Redis + L2 pgvector)。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §3
"""

import json
import logging

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
        except Exception as e:
            logger.warning("qr l1 get failed: %s: %s", type(e).__name__, e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("qr l1 payload not json, dropping")
            return None

    async def set(self, query_hash: str, payload: dict) -> None:
        try:
            await self._redis.setex(
                self._key(query_hash),
                self._ttl,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as e:
            logger.warning("qr l1 set failed: %s: %s", type(e).__name__, e)

    async def delete(self, query_hash: str) -> None:
        try:
            await self._redis.delete(self._key(query_hash))
        except Exception as e:
            logger.warning("qr l1 delete failed: %s: %s", type(e).__name__, e)
