"""Tests for DocIngestionPipeline.run()."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.api.schemas.ingestion import (
    DocIngestionRequest,
    DocIngestionOptions,
    DocIngestionSource,
    IngestionResult,
)
from spma.ingestion.doc_pipeline import DocIngestionPipeline
from spma.ingestion.source_handlers.base import SourceDocument


class _AsyncIterator:
    """Helper to create an async iterator from a list."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return self._items.pop(0)
        except IndexError:
            raise StopAsyncIteration


def _make_fetch_documents(docs):
    """Create a mock fetch_documents that returns an async iterator."""
    def fetch_documents(request):
        return _AsyncIterator(list(docs))
    return fetch_documents


class TestRun:
    """Tests for the run() method."""

    @pytest.mark.asyncio
    async def test_run_with_markdown_handler_success(self):
        """Full mode: handler yields documents, pipeline ingests them."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc1 = SourceDocument(
            text="# Doc 1",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc1",
        )
        doc2 = SourceDocument(
            text="# Doc 2",
            source_id="def456",
            source_type="markdown_dir",
            page_title="doc2",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([doc1, doc2])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        pipeline.update_document = AsyncMock(return_value=3)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/data/docs",
        )

        result = await pipeline.run(request)

        assert result.status == "completed"
        assert result.stats["files_processed"] == 2
        assert result.stats["chunks_generated"] == 6
        assert result.stats["errors"] == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_run_full_mode_uses_update_document(self):
        """Full mode should call update_document (delete + re-ingest)."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([doc])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock()
        pipeline.update_document = AsyncMock(return_value=5)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/data/docs",
        )

        await pipeline.run(request)

        pipeline.update_document.assert_called_once()
        pipeline.ingest_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_incremental_mode_uses_ingest_document(self):
        """Incremental mode should call ingest_document (direct write)."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([doc])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock(return_value=3)
        pipeline.update_document = AsyncMock()

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
        )

        await pipeline.run(request)

        pipeline.ingest_document.assert_called_once()
        pipeline.update_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_force_full_reindex_uses_update_document(self):
        """force_full_reindex should trigger update_document even in incremental mode."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([doc])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock()
        pipeline.update_document = AsyncMock(return_value=3)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
            options=DocIngestionOptions(force_full_reindex=True),
        )

        await pipeline.run(request)

        pipeline.update_document.assert_called_once()
        pipeline.ingest_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_partial_failure_continues(self):
        """Single document failure should not stop processing remaining docs."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc1 = SourceDocument(text="# OK", source_id="ok1", source_type="markdown_dir", page_title="ok")
        doc2 = SourceDocument(text="# Bad", source_id="bad1", source_type="markdown_dir", page_title="bad")
        doc3 = SourceDocument(text="# OK2", source_id="ok2", source_type="markdown_dir", page_title="ok2")

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([doc1, doc2, doc3])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated failure")
            return 2

        pipeline.ingest_document = AsyncMock(side_effect=side_effect)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
        )

        result = await pipeline.run(request)

        assert result.status == "completed_with_errors"
        assert result.stats["files_processed"] == 2
        assert result.stats["errors"] == 1
        assert len(result.errors) == 1
        assert result.errors[0]["source_id"] == "bad1"

    @pytest.mark.asyncio
    async def test_run_unsupported_source(self):
        """Unsupported source returns failed result."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={},
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
        )

        result = await pipeline.run(request)

        assert result.status == "failed"
        assert len(result.errors) == 1
        assert "Unsupported source" in result.errors[0]["error"]

    @pytest.mark.asyncio
    async def test_run_no_documents(self):
        """Handler yielding no documents is not an error."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        mock_handler = MagicMock()
        mock_handler.fetch_documents = _make_fetch_documents([])

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/empty/dir",
        )

        result = await pipeline.run(request)

        assert result.status == "completed"
        assert result.stats["files_processed"] == 0
        assert result.stats["chunks_generated"] == 0
