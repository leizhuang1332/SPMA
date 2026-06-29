"""端到端:从 query 进,断言 normalized 含 canonical_term。"""
import os
import pytest

from spma.agents.supervisor import graph as graph_mod


pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="DATABASE_URL not set; skipping DB-dependent integration test",
)


@pytest.fixture
async def seeded_pool(db_pool):
    """插入 1 条 active synonym 后清空。"""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM synonym_map")
        await conn.execute(
            """
            INSERT INTO synonym_map
                (user_term, canonical_term, source, confidence, status)
            VALUES ($1, $2, $3, $4, $5)
            """,
            "买啥", "商品列表", "test", 0.9, "active",
        )
    yield db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM synonym_map WHERE source = 'test'")


@pytest.mark.asyncio
async def test_e2e_synonym_replacement(seeded_pool):
    """含 user_term 的 query 经 _normalize_with_synonyms 后,normalized 应含 canonical_term。"""
    from spma.agents.supervisor.query_rewriter import _normalize_with_synonyms

    synonym_map = {"买啥": ["商品列表"]}
    result = await _normalize_with_synonyms("我想知道买啥", synonym_map, {})
    assert "商品列表" in result, f"expected canonical in normalized, got: {result}"


@pytest.mark.asyncio
async def test_e2e_db_loaded_synonym_via_synonymmap(seeded_pool):
    """验证 SynonymMap.query() 加载的 dict 格式可被 _normalize_with_synonyms 消费。"""
    from spma.ingestion.synonym_map import SynonymMap
    from spma.agents.supervisor.query_rewriter import _normalize_with_synonyms

    syn_map = SynonymMap(seeded_pool)
    db_result = await syn_map.query(status="active", limit=10)
    assert db_result["total"] == 1

    # 转为 dict[user_term, list[canonical_term]]
    synonym_map = {}
    for e in db_result["entries"]:
        synonym_map.setdefault(e["user_term"], []).append(e["canonical_term"])
    assert synonym_map == {"买啥": ["商品列表"]}

    # 喂给下游
    result = await _normalize_with_synonyms("买啥在哪", synonym_map, {})
    assert "商品列表" in result


@pytest.mark.asyncio
async def test_e2e_graph_load_synonym_map_with_seeded_db(seeded_pool, monkeypatch):
    """端到端:graph._load_synonym_map 从 DB 加载并组装 dict。"""
    # 设置 db_pool(模拟 graph.py 启动时的 set_db_pool)
    from spma.api.dependencies import set_db_pool
    monkeypatch.setattr("spma.api.dependencies._db_pool", None)  # NEW: 防止跨测试污染
    set_db_pool(seeded_pool)

    result = await graph_mod._load_synonym_map()
    assert result == {"买啥": ["商品列表"]}, f"got: {result}"