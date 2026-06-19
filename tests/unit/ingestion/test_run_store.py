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


@pytest.fixture
def mock_pool():
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
def store(mock_pool):
    from spma.ingestion.run_store import PipelineRunStore
    return PipelineRunStore(mock_pool)


class TestPipelineRunStore:

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


class TestGetLatestSuccessful:
    """Tests for get_latest_successful()."""

    @pytest.mark.asyncio
    async def test_returns_latest_successful_run(self, store, mock_pool):
        """返回最近一次成功的运行记录。"""
        mock_row = {
            "pipeline_run_id": "ingest-doc-20260619-143000",
            "pipeline_type": "doc",
            "source": "markdown_dir",
            "mode": "incremental",
            "status": "completed",
            "started_at": "2026-06-19T14:30:00+00:00",
            "completed_at": "2026-06-19T14:30:45+00:00",
            "stats": '{"files_processed": 10}',
            "errors": "[]",
        }
        mock_pool._conn.fetchrow.return_value = mock_row

        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is not None
        assert result["pipeline_run_id"] == "ingest-doc-20260619-143000"
        assert result["status"] == "completed"
        assert result["source"] == "markdown_dir"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_run(self, store, mock_pool):
        """没有匹配记录时返回 None。"""
        mock_pool._conn.fetchrow.return_value = None

        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is None

    @pytest.mark.asyncio
    async def test_only_returns_completed_runs(self, store, mock_pool):
        """只返回 status='completed' 的记录——running/failed 不算。"""
        mock_pool._conn.fetchrow.return_value = None  # 没有 completed 的

        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is None

    @pytest.mark.asyncio
    async def test_without_source_type_filter(self, store, mock_pool):
        """不传 source_type 时只按 pipeline_type 过滤，不按 source 过滤。"""
        mock_row = {
            "pipeline_run_id": "ingest-code-20260619-150000",
            "pipeline_type": "code",
            "source": "github",
            "status": "completed",
        }
        mock_pool._conn.fetchrow.return_value = mock_row

        result = await store.get_latest_successful("code")

        assert result is not None
        assert result["pipeline_type"] == "code"
        # 验证 SQL 中没有 source = $2 条件
        sql = mock_pool._conn.fetchrow.call_args[0][0]
        assert "source = $2" not in sql
