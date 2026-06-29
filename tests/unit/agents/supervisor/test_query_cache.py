"""QueryCache 整合:lookup_or_compute 走 L1→L2→compute 链路。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.query_cache import QueryCache


@pytest.mark.asyncio
async def test_lookup_returns_l1_payload_without_touching_l2():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value={"rewrite": "L1"})
    l2 = AsyncMock()
    pool = MagicMock()

    qc = QueryCache(l1=l1, l2=l2, pool=pool, embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "COMPUTE"}),
    )
    assert out["rewrite"] == "L1"
    assert out["cache_layer"] == "l1"
    l2.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_falls_through_to_l2_when_l1_misses():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value={
        "payload": {"rewrite": "L2"},
        "match_type": "semantic_match",
        "cosine_distance": 0.04,
    })

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(),
    )
    assert out["rewrite"] == "L2"
    assert out["cache_layer"] == "l2"
    # L1 应被回填
    l1.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_falls_through_to_compute_when_both_miss():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 1024)

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=embedder)
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "COMPUTE"}),
    )
    assert out["rewrite"] == "COMPUTE"
    assert out["cache_layer"] == "miss"
    # compute 触发后必须 L1+L2 都被回填
    l1.set.assert_awaited_once()
    # L2 是通过 asyncio.create_task 异步触发,等待 task 完成
    await asyncio.sleep(0)
    l2.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_does_not_cache_when_compute_times_out():
    """compute 超时时,绝不能写 L1/L2。"""
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 1024)

    async def timeout_compute(*a, **kw):
        raise asyncio.TimeoutError()

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=embedder)
    with pytest.raises(asyncio.TimeoutError):
        await qc.lookup_or_compute(
            query="q", history_fingerprint="fp", entities={},
            weights_version=1, synonym_version=1,
            compute=timeout_compute,
        )
    l1.set.assert_not_awaited()
    l2.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_skips_l2_when_query_contains_pii(caplog):
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(),
                    embedder=AsyncMock(embed_query=AsyncMock(return_value=[0.0]*4)))
    out = await qc.lookup_or_compute(
        query="手机号 13812345678 怎么改",
        history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "OK"}),
    )
    assert out["cache_layer"] == "miss"
    l2.lookup.assert_not_awaited()  # PII 路径直接走 compute


@pytest.mark.asyncio
async def test_lookup_degrades_when_l1_raises_connection_error():
    from redis.exceptions import ConnectionError
    l1 = AsyncMock()
    l1.get = AsyncMock(side_effect=ConnectionError("redis down"))
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value={
        "payload": {"rewrite": "L2"}, "match_type": "semantic_match",
    })

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(),
    )
    assert out["rewrite"] == "L2"