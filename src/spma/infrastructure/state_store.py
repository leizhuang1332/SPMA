"""三层状态存储——进程内存 -> Redis热状态 -> PostgreSQL冷trace。

降级: Redis 不可用 -> 自动降级到进程内存，标注 degradation level
"""

import json
import time
import logging
import uuid
from typing import Protocol

logger = logging.getLogger(__name__)


class StateStorageProtocol(Protocol):
    async def save(self, key: str, state: dict, ttl: int | None = None) -> None: ...
    async def load(self, key: str) -> dict | None: ...
    async def delete(self, key: str) -> None: ...
    async def health_check(self) -> bool: ...


class InMemoryStateStore:
    def __init__(self):
        self._store: dict[str, tuple[dict, float]] = {}

    async def save(self, key: str, state: dict, ttl: int = 300) -> None:
        expires_at = time.time() + ttl
        self._store[key] = (state, expires_at)

    async def load(self, key: str) -> dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        state, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return state

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def health_check(self) -> bool:
        return True


class RedisStateStore:
    def __init__(self, redis_client, default_ttl: int = 300):
        self._redis = redis_client
        self.default_ttl = default_ttl
        self._fallback = InMemoryStateStore()
        self._degraded = False

    async def save(self, key: str, state: dict, ttl: int | None = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        value = json.dumps(state, ensure_ascii=False)
        try:
            await self._redis.setex(key, ttl, value.encode("utf-8"))
        except Exception as e:
            logger.warning(f"Redis 保存失败 ({key}): {e}，降级到内存")
            self._degraded = True
            await self._fallback.save(key, state, ttl)

    async def load(self, key: str) -> dict | None:
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Redis 加载失败 ({key}): {e}，尝试内存降级")
            self._degraded = True
            return await self._fallback.load(key)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Redis 删除失败 ({key}): {e}")
        await self._fallback.delete(key)

    async def health_check(self) -> bool:
        try:
            if await self._redis.ping():
                if self._degraded:
                    logger.info("Redis 已恢复，切回 Redis 主存储")
                    self._degraded = False
                return True
        except Exception:
            pass
        self._degraded = True
        return False

    @property
    def is_degraded(self) -> bool:
        return self._degraded


class ConfirmationTokenStore:
    def __init__(self, state_store=None):
        self._store = state_store or InMemoryStateStore()

    async def save(self, state: dict, ttl_seconds: int = 180) -> str:
        token = f"tok_{uuid.uuid4().hex[:12]}"
        await self._store.save(f"confirmation:{token}", {"state": state, "expires_at": time.time() + ttl_seconds}, ttl=ttl_seconds)
        return token

    async def load(self, token: str) -> dict | None:
        return await self._store.load(f"confirmation:{token}")

    async def delete(self, token: str) -> None:
        await self._store.delete(f"confirmation:{token}")
