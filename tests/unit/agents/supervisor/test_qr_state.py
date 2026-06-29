"""qr_state_meta 读写 + cache_key 构造单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.supervisor.qr_state import (
    build_cache_key,
    bump_weights_version,
    get_versions,
)


@pytest.mark.asyncio
async def test_get_versions_returns_current_state():
    """get_versions 必须返回 (weights_version, synonym_version)。"""
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"weights_version": 7, "synonym_version": 3})
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    w, s = await get_versions(pool)
    assert w == 7
    assert s == 3


def test_cache_key_differs_when_weights_version_bumps():
    """权重版本号变化必须改变 cache_key。"""
    base = build_cache_key(
        query="怎么取消订单",
        history_fingerprint="abc",
        entities={},
        weights_version=1,
        synonym_version=1,
    )
    bumped = build_cache_key(
        query="怎么取消订单",
        history_fingerprint="abc",
        entities={},
        weights_version=2,
        synonym_version=1,
    )
    assert base != bumped
    assert len(base) == 32  # md5 hex


def test_cache_key_differs_when_synonym_version_bumps():
    """synonym 版本号变化必须改变 cache_key。"""
    base = build_cache_key(query="q", history_fingerprint="h", entities={},
                           weights_version=1, synonym_version=1)
    bumped = build_cache_key(query="q", history_fingerprint="h", entities={},
                             weights_version=1, synonym_version=2)
    assert base != bumped


def test_cache_key_is_deterministic_for_same_input():
    """相同输入必须产生相同 key。"""
    k1 = build_cache_key(query="q", history_fingerprint="h", entities={"a": 1},
                         weights_version=1, synonym_version=1)
    k2 = build_cache_key(query="q", history_fingerprint="h", entities={"a": 1},
                         weights_version=1, synonym_version=1)
    assert k1 == k2


def test_history_fingerprint_only_uses_last_3_turns():
    """history_fingerprint 仅依赖最近 3 轮输入(由调用方保证)。"""
    fp_long = "deadbeef" * 4
    fp_short = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    a = build_cache_key("q", history_fingerprint=fp_long, entities={},
                        weights_version=1, synonym_version=1)
    b = build_cache_key("q", history_fingerprint=fp_short, entities={},
                        weights_version=1, synonym_version=1)
    assert a == b


@pytest.mark.asyncio
async def test_bump_weights_version_returns_new_number():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=42)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    new_v = await bump_weights_version(pool)
    assert new_v == 42
