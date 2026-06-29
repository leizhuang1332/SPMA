"""L1 Redis 精确缓存单元测试 + 故障降级。"""

import json
from unittest.mock import AsyncMock

import pytest

from spma.agents.supervisor.query_cache import L1Cache


@pytest.mark.asyncio
async def test_l1_get_returns_payload_on_hit():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b'{"rewrite":"x","candidates":["x"]}')
    l1 = L1Cache(redis)
    out = await l1.get("deadbeef")
    assert out == {"rewrite": "x", "candidates": ["x"]}


@pytest.mark.asyncio
async def test_l1_get_returns_none_on_miss():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    l1 = L1Cache(redis)
    assert await l1.get("deadbeef") is None


@pytest.mark.asyncio
async def test_l1_get_transparently_falls_back_on_connection_error():
    from redis.exceptions import ConnectionError

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    l1 = L1Cache(redis)
    out = await l1.get("deadbeef")
    assert out is None  # 不抛异常,健康降级
    redis.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_l1_set_calls_setex_with_ttl():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    l1 = L1Cache(redis, ttl_s=3600)
    await l1.set("deadbeef", {"rewrite": "x"})
    redis.setex.assert_awaited_once()
    args = redis.setex.await_args.args
    assert args[0] == "qr:exact:deadbeef"
    assert args[1] == 3600
    payload = json.loads(args[2].decode())
    assert payload == {"rewrite": "x"}


@pytest.mark.asyncio
async def test_l1_set_swallows_connection_errors(caplog):
    """Redis 不可用时写 L1 不应阻塞 hot path。"""
    from redis.exceptions import ConnectionError

    redis = AsyncMock()
    redis.setex = AsyncMock(side_effect=ConnectionError("redis down"))
    l1 = L1Cache(redis)
    await l1.set("deadbeef", {"rewrite": "x"})  # 不抛异常
