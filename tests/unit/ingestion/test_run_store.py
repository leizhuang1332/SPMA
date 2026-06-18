import pytest
from unittest.mock import AsyncMock


class _MockConnectionContext:
    """模拟 async with pool.acquire() 的上下文管理器。"""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class TestPipelineRunStore:
    @pytest.fixture
    def mock_pool(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock()
        conn.fetch = AsyncMock()

        def mock_acquire():
            return _MockConnectionContext(conn)

        pool.acquire = mock_acquire
        pool._conn = conn
        return pool

    @pytest.fixture
    def store(self, mock_pool):
        from spma.ingestion.run_store import PipelineRunStore
        return PipelineRunStore(mock_pool)

    @pytest.mark.asyncio
    async def test_create_returns_run_id(self, store, mock_pool):
        mock_pool._conn.fetchrow.return_value = {"pipeline_run_id": "ingest-doc-20260607-102345"}

        run_id = await store.create("doc", "confluence", "incremental", "manual")

        assert run_id.startswith("ingest-doc-")
        assert "2026" in run_id

    @pytest.mark.asyncio
    async def test_update_writes_stats(self, store, mock_pool):
        mock_pool._conn.execute.return_value = "UPDATE 1"

        await store.update(
            run_id="ingest-doc-20260607-102345",
            status="completed",
            stats={"pages_processed": 10},
            errors=[],
            completed_at="2026-06-07T10:27:30Z",
        )

        assert mock_pool._conn.execute.called

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown_id(self, store, mock_pool):
        mock_pool._conn.fetchrow.return_value = None

        result = await store.get("unknown-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_latest_returns_most_recent(self, store, mock_pool):
        mock_pool._conn.fetchrow.return_value = {
            "pipeline_run_id": "ingest-doc-20260607-102345",
            "pipeline_type": "doc",
            "status": "completed",
        }

        result = await store.get_latest("doc")
        assert result["pipeline_type"] == "doc"

    @pytest.mark.asyncio
    async def test_list_recent_respects_limit(self, store, mock_pool):
        mock_pool._conn.fetch.return_value = [
            {"pipeline_run_id": f"ingest-doc-{i:02d}"} for i in range(5)
        ]

        results = await store.list_recent(limit=5)
        assert len(results) == 5
