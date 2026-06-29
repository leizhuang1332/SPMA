"""验证 002 迁移文件应用成功 + 三张表存在 + HNSW 索引可创建。"""

import pytest
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "deployments/docker/migrations/002_qr_cache_and_state.sql"
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_creates_three_tables(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE tablename IN "
            "('qr_weights_history','qr_state_meta','qr_cache_entries')"
        )
        names = {r["tablename"] for r in rows}
        assert {"qr_weights_history", "qr_state_meta", "qr_cache_entries"} <= names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_meta_has_single_row(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        row = await conn.fetchrow("SELECT weights_version, synonym_version FROM qr_state_meta")
        assert row["weights_version"] == 1
        assert row["synonym_version"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hnsw_index_usable(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE indexname='idx_qr_cache_hnsw'"
        )
        assert row is not None
        assert "hnsw" in row["indexdef"].lower()