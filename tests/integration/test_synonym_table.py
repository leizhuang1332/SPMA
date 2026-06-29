"""验证 migration 004 部署后 synonym_map 表结构正确。"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="DATABASE_URL not set; skipping DB-dependent integration test",
)


@pytest.mark.asyncio
async def test_synonym_table_exists(db_pool):
    """迁移部署后,表必须存在。"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT to_regclass('synonym_map') AS table_exists"
        )
    assert row["table_exists"] == "synonym_map"


@pytest.mark.asyncio
async def test_synonym_table_has_expected_columns(db_pool):
    """验证表的列结构与 SynonymMap.query() 返回字段对齐。"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'synonym_map'
            ORDER BY ordinal_position
            """
        )
    columns = {r["column_name"]: r["data_type"] for r in rows}
    expected = {
        "id", "user_term", "canonical_term", "category", "source",
        "confidence", "status", "hits_30d", "last_triggered_at",
        "created_at", "updated_at",
    }
    assert expected.issubset(set(columns.keys())), \
        f"missing columns: {expected - set(columns.keys())}"