"""L2 pgvector 单 SQL 双召回 + PII skip 单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.supervisor.query_cache import L2Cache, contains_pii


@pytest.mark.parametrize(
    "text",
    [
        "我的手机号是 13812345678 怎么改",
        "身份证 110101199003078888",
        "邮箱 user@example.com 怎么联系",
    ],
)
def test_contains_pii_detects_phone_id_email(text):
    assert contains_pii(text) is True


@pytest.mark.parametrize(
    "text",
    ["订单取消怎么操作", "怎么查询 user_id=42 的订单", "表 user_orders 是什么"],
)
def test_contains_pii_returns_false_for_clean_text(text):
    assert contains_pii(text) is False


@pytest.mark.asyncio
async def test_l2_lookup_returns_exact_match_first():
    """精确 hash 命中应优先于语义近邻。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "payload": {"rewrite": "EXACT"},
        "match_type": "exact_match",
        "cosine_distance": 0.0,
    })
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    out = await l2.lookup(
        query_hash="abc", query_embedding=[0.0] * 1024,
        weights_version=1, synonym_version=1,
    )
    assert out == {"payload": {"rewrite": "EXACT"}, "match_type": "exact_match"}


@pytest.mark.asyncio
async def test_l2_lookup_returns_none_on_miss():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    out = await l2.lookup(
        query_hash="miss", query_embedding=[0.0] * 1024,
        weights_version=1, synonym_version=1,
    )
    assert out is None


@pytest.mark.asyncio
async def test_l2_lookup_passes_versions_into_sql():
    """SQL 必须用传入的 weights/synonym version 过滤。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    await l2.lookup(
        query_hash="abc", query_embedding=[0.0] * 1024,
        weights_version=7, synonym_version=3,
    )

    sql = conn.fetchrow.await_args.args[0]
    assert "weights_version" in sql
    assert "synonym_version" in sql
    # values must include version 7 and 3
    assert 7 in conn.fetchrow.await_args.args


@pytest.mark.asyncio
async def test_l2_put_skips_when_query_contains_pii(caplog):
    """含 PII 的 query 必须跳过 L2 写入。"""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=4)
    await l2.put(
        query="我的手机号 13812345678",
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        payload={"rewrite": "x"},
        weights_version=1,
        synonym_version=1,
        query_hash="abc",
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_l2_put_inserts_when_clean():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=42)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=4)
    new_id = await l2.put(
        query="取消订单流程",
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        payload={"rewrite": "订单取消流程"},
        weights_version=1,
        synonym_version=1,
        query_hash="abc",
    )
    assert new_id == 42
    conn.fetchval.assert_awaited_once()
