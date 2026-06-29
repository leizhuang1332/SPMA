"""rewrite_queries 接入 QueryCache + QrAuditBuffer 后行为。"""

import pytest
from unittest.mock import AsyncMock

from spma.agents.supervisor import query_rewriter


@pytest.mark.asyncio
async def test_rewrite_queries_passes_through_cache_when_provided():
    cache = AsyncMock()
    cache.lookup_or_compute = AsyncMock(return_value={
        "original": "q", "normalized": "q", "resolved": "q",
        "expanded": "Q", "doc": "Q", "code": "Q",
        "cache_layer": "l1",
    })
    audit = AsyncMock()
    audit.enqueue = AsyncMock()

    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc", "code"], "query_type": "search",
                        "is_cross_source": False},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        cache=cache,
        audit_buffer=audit,
        weights_version=1,
        synonym_version=2,
    )
    cache.lookup_or_compute.assert_awaited_once()
    assert out["cache_layer"] == "l1"


@pytest.mark.asyncio
async def test_rewrite_queries_records_audit_with_cache_layer():
    cache = AsyncMock()
    cache.lookup_or_compute = AsyncMock(return_value={
        "original": "q", "normalized": "q", "resolved": "q",
        "expanded": "Q", "doc": "Q", "code": "Q",
        "cache_layer": "l2",
    })
    audit = AsyncMock()
    audit.enqueue = AsyncMock()

    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc", "code"], "query_type": "search"},
        entities={},
        llm=None, synonym_map=None, conversation_history="",
        cache=cache, audit_buffer=audit, weights_version=1, synonym_version=1,
    )
    audit.enqueue.assert_awaited_once()
    record = audit.enqueue.await_args.args[0]
    assert record["stage"] == "rewrite"
    assert record["cache_layer"] == "l2"
    assert record["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_rewrite_queries_skips_cache_when_not_provided():
    """无 cache 参数时保持原 5 阶段管道行为。"""
    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc"], "query_type": "search"},
        entities={}, llm=None, synonym_map=None, conversation_history="",
    )
    assert out["original"] == "q"
    assert "cache_layer" not in out
