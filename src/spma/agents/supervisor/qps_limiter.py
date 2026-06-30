"""基于 tenant_id + user_id 的 QPS 限流(Redis 滑动窗口)。"""
import logging
import time

logger = logging.getLogger(__name__)


class QPSLimiter:
    """Redis 滑动窗口(1 秒)。"""

    def __init__(self, redis_client, default_qps: int = 10, vip_tenants: set[str] | None = None):
        self._redis = redis_client
        self._default = default_qps
        self._vip = vip_tenants or {"vip_internal", "vip_partner"}
        self._vip_qps = 50

    async def check(self, tenant_id: str, user_id: str) -> bool:
        limit = self._vip_qps if tenant_id in self._vip else self._default
        key = f"qps:qr:{tenant_id}:{user_id}"
        now = time.time()
        window_start = now - 1.0
        await self._redis.zremrangebyscore(key, 0, window_start)
        count = await self._redis.zcard(key)
        if count >= limit:
            return False
        await self._redis.zadd(key, {f"{now}": now})
        await self._redis.expire(key, 2)
        return True
