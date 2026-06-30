"""策略级 feature flag,支持秒级 KILL SWITCH(主文件 §3.8 + ADR-010)。"""
import json
import logging
import time

logger = logging.getLogger(__name__)


class StrategyFeatureFlag:
    """策略级 feature flag。

    - Redis 存 `{enabled: bool, rollout_pct: 0-100}`
    - 本地缓存 5s,避免每请求打 Redis
    - set_rollout() 立即清空本地缓存,5s 内全网生效
    """

    def __init__(self, redis_client, local_cache_ttl: int = 5):
        self._redis = redis_client
        self._cache: dict[str, tuple[bool, float]] = {}
        self._ttl = local_cache_ttl

    async def is_enabled(self, strategy_name: str, user_bucket: int | None = None) -> bool:
        cache_key = f"{strategy_name}:{user_bucket}"
        now = time.time()
        if cache_key in self._cache:
            enabled, expires_at = self._cache[cache_key]
            if expires_at > now:
                return enabled

        flag = await self._redis.get(f"flag:qr:{strategy_name}")
        if not flag:
            enabled = True
        else:
            config = json.loads(flag)
            enabled = config.get("enabled", True)
            if user_bucket is not None and "rollout_pct" in config:
                enabled = enabled and (user_bucket < config["rollout_pct"])

        self._cache[cache_key] = (enabled, now + self._ttl)
        return enabled

    async def set_rollout(self, strategy_name: str, *, rollout_pct: int, enabled: bool = True):
        await self._redis.set(
            f"flag:qr:{strategy_name}",
            json.dumps({"enabled": enabled, "rollout_pct": rollout_pct}),
        )
        self._cache.clear()
        logger.info(
            "FeatureFlag %s: enabled=%s, rollout_pct=%s",
            strategy_name, enabled, rollout_pct,
        )

    def user_bucket(self, user_id: str) -> int:
        return hash(user_id) % 100
