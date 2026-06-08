import pytest
import time
from spma.infrastructure.state_store import RedisStateStore, InMemoryStateStore


class FakeRedis:
    def __init__(self):
        self._store: dict[str, bytes] = {}
        self._ttl: dict[str, float] = {}
        self.healthy = True

    async def get(self, key: str) -> bytes | None:
        exp = self._ttl.get(key, float("inf"))
        if time.time() > exp:
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self._store[key] = value
        self._ttl[key] = time.time() + ttl

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttl.pop(key, None)

    async def ping(self) -> bool:
        return self.healthy

    async def close(self) -> None:
        pass


class TestRedisStateStore:
    async def test_save_and_load_round_trip(self):
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)
        state = {"round": 2, "accumulated_chunk_ids": ["uuid-abc", "uuid-def"]}
        key = "agent:user-001:sess-abc:qry-xyz:doc:state"
        await store.save(key, state, ttl=300)
        loaded = await store.load(key)
        assert loaded == state

    async def test_load_expired_returns_none(self):
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)
        key = "agent:user-001:sess-abc:qry-xyz:doc:state"
        await store.save(key, {"round": 1}, ttl=0)
        time.sleep(0.1)
        loaded = await store.load(key)
        assert loaded is None

    async def test_delete_removes_state(self):
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)
        key = "agent:user-001:sess-abc:qry-xyz:doc:state"
        await store.save(key, {"round": 1}, ttl=300)
        await store.delete(key)
        loaded = await store.load(key)
        assert loaded is None


class TestInMemoryDegradation:
    async def test_fallback_when_redis_unavailable(self):
        redis = FakeRedis()
        redis.healthy = False
        store = RedisStateStore(redis_client=redis)
        health = await store.health_check()
        assert health is False
        mem_store = InMemoryStateStore()
        await mem_store.save("test_key", {"data": "fallback"})
        loaded = await mem_store.load("test_key")
        assert loaded == {"data": "fallback"}

    async def test_in_memory_no_persistence(self):
        store_a = InMemoryStateStore()
        store_b = InMemoryStateStore()
        await store_a.save("key", {"from": "store_a"})
        loaded = await store_b.load("key")
        assert loaded is None
