"""Redis 缓存——热点问答(TTL=1h) + 查询结果(TTL=5min) + LLM翻译(TTL=24h)。

L4 降级时，热点问答缓存作为兜底读取路径。

设计依据: API-06 §2 缓存契约
"""

import json
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CacheService:
    """Redis 缓存服务。

    L4 降级时 fallback_enabled=True，主读取路径切换为缓存。
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self.fallback_enabled = False
        self._cached_qa: list[dict] = []

    # === 热点问答缓存 ===

    async def cache_qa(self, question: str, answer: str, ttl: int = 3600) -> None:
        """缓存热点问答。"""
        key = self._qa_key(question)
        value = json.dumps({"q": question, "a": answer}, ensure_ascii=False)
        if self._redis:
            try:
                await self._redis.setex(key, ttl, value.encode("utf-8"))
            except Exception:
                logger.warning("Redis QA 缓存写入失败")
        self._cached_qa.append({"q": question, "a": answer})

    async def get_cached_qa(self, question: str) -> dict | None:
        """查询缓存的问答。"""
        key = self._qa_key(question)
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        for qa in self._cached_qa:
            if qa["q"] == question:
                return qa
        return None

    async def get_all_cached_qa(self) -> list[dict]:
        """获取所有缓存的问答（L4 兜底用）。"""
        if self._redis:
            try:
                keys = await self._redis.keys("cache:qa:*")
                if keys:
                    values = await self._redis.mget(keys)
                    results = []
                    for v in values:
                        if v:
                            try:
                                results.append(json.loads(v))
                            except json.JSONDecodeError:
                                pass
                    if results:
                        return results
            except Exception:
                logger.warning("Redis QA 全量读取失败")
        return list(self._cached_qa)

    def get_cached_qa_count(self) -> int:
        """缓存问答数量（L4 前置检查用）。"""
        return len(self._cached_qa)

    # === 降级兜底 ===

    def enable_fallback(self) -> None:
        """启用缓存兜底（L4 触发）。"""
        self.fallback_enabled = True
        logger.warning("缓存兜底启用")

    def disable_fallback(self) -> None:
        """禁用缓存兜底（L4 恢复）。"""
        self.fallback_enabled = False
        logger.info("缓存兜底关闭")

    def is_fallback_active(self) -> bool:
        return self.fallback_enabled

    # === 查询结果缓存 ===

    async def cache_result(self, query_id: str, result: dict,
                          ttl: int = 300) -> None:
        """缓存查询结果。"""
        key = f"cache:result:{query_id}"
        if self._redis:
            try:
                await self._redis.setex(
                    key, ttl, json.dumps(result, ensure_ascii=False).encode("utf-8")
                )
            except Exception:
                pass

    async def get_cached_result(self, query_id: str) -> dict | None:
        """获取缓存的查询结果。"""
        key = f"cache:result:{query_id}"
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        return None

    # === LLM 翻译缓存 ===

    async def cache_translation(self, zh_term: str, en_term: str,
                               ttl: int = 86400) -> None:
        """缓存中英文翻译。"""
        key = f"cache:llm_trans:{zh_term}"
        if self._redis:
            try:
                await self._redis.setex(key, ttl, en_term.encode("utf-8"))
            except Exception:
                pass

    # === helpers ===

    @staticmethod
    def _qa_key(question: str) -> str:
        h = hashlib.md5(question.encode()).hexdigest()[:12]
        return f"cache:qa:{h}"


# 全局单例
_cache_service: CacheService | None = None


def get_cache_service() -> CacheService:
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service
