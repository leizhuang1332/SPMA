"""Integration tests for OneswikiSourceHandler with mocked HTTP responses."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
from spma.ingestion.source_handlers.oneswiki_handler import OneswikiSourceHandler


@pytest.fixture
def run_store():
    store = MagicMock()
    store.get_latest_successful = AsyncMock(return_value=None)
    return store


@pytest.fixture
def sample_pages_response():
    return {
        "pages": [
            {"uuid": "root", "parent_uuid": "", "title": "Root"},
            {"uuid": "p1", "parent_uuid": "root", "title": "Page 1",
             "updated_time": 1700000100, "version": 1},
            {"uuid": "p2", "parent_uuid": "root", "title": "Page 2",
             "updated_time": 1700000200, "version": 2},
            {"uuid": "p1child", "parent_uuid": "p1", "title": "Page 1 Child",
             "updated_time": 1700000300, "version": 1},
            {"uuid": "orphan", "parent_uuid": "other", "title": "Orphan",
             "updated_time": 1700000400, "version": 1},
        ]
    }


@pytest.fixture
def valid_config():
    return {
        "auth_token": "tok",
        "cookie": "ck",
        "team_uuid": "team1",
        "space_uuid": "space1",
        "parent_uuid": "root",
        "concurrency": 1,  # sequential for predictable test ordering
    }


def _make_response(json_data):
    """Helper to create a mock httpx response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


def _page_content(uuid, title, content, version=1, updated_time=1700000100):
    return {
        "uuid": uuid, "title": title, "content": content,
        "version": version, "updated_time": updated_time,
    }


class TestFetchDocumentsIntegration:
    """Full fetch_documents flow with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_full_flow_yields_documents(self, run_store, sample_pages_response, valid_config):
        handler = OneswikiSourceHandler(run_store=run_store, config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config=valid_config,
        )

        mock_client = MagicMock()
        mock_client.get = AsyncMock()
        mock_client.get.side_effect = [
            _make_response(sample_pages_response),
            _make_response(_page_content("p1", "Page 1", "<h1>Hello</h1>")),
            _make_response(_page_content("p2", "Page 2", "<h2>P2 Content</h2>", version=2, updated_time=1700000200)),
            _make_response(_page_content("p1child", "Page 1 Child", "<p>Child</p>", updated_time=1700000300)),
        ]

        mock_aclient = MagicMock()
        mock_aclient.__aenter__ = AsyncMock(return_value=mock_client)
        mock_aclient.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_aclient):
            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

        assert len(docs) == 3  # p1, p2, p1child (not orphan)
        titles = {d.page_title for d in docs}
        assert titles == {"Page 1", "Page 2", "Page 1 Child"}
        for d in docs:
            assert d.source_type == DocIngestionSource.ONES_WIKI
            assert d.source_id in ("p1", "p2", "p1child")

    @pytest.mark.asyncio
    async def test_empty_subtree_returns_no_documents(self, run_store, valid_config):
        handler = OneswikiSourceHandler(run_store=run_store, config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={**valid_config, "parent_uuid": "nonexistent"},
        )

        empty_response = {"pages": [{"uuid": "x", "parent_uuid": "y", "title": "X"}]}

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_make_response(empty_response))

        mock_aclient = MagicMock()
        mock_aclient.__aenter__ = AsyncMock(return_value=mock_client)
        mock_aclient.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_aclient):
            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

        assert len(docs) == 0

    @pytest.mark.asyncio
    async def test_single_page_failure_does_not_break_others(self, run_store, sample_pages_response, valid_config):
        handler = OneswikiSourceHandler(run_store=run_store, config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config=valid_config,
        )

        mock_client = MagicMock()
        mock_client.get = AsyncMock()
        mock_client.get.side_effect = [
            _make_response(sample_pages_response),
            Exception("Network error"),  # p1 fails
            _make_response(_page_content("p2", "Page 2", "<p>OK</p>", updated_time=1700000200)),
            _make_response(_page_content("p1child", "Page 1 Child", "<p>Also OK</p>", updated_time=1700000300)),
        ]

        mock_aclient = MagicMock()
        mock_aclient.__aenter__ = AsyncMock(return_value=mock_client)
        mock_aclient.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_aclient):
            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

        assert len(docs) == 2
        titles = {d.page_title for d in docs}
        assert titles == {"Page 2", "Page 1 Child"}

    @pytest.mark.asyncio
    async def test_page_list_api_failure_raises(self, run_store, valid_config):
        handler = OneswikiSourceHandler(run_store=run_store, config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config=valid_config,
        )

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=Exception("API down"))

        mock_aclient = MagicMock()
        mock_aclient.__aenter__ = AsyncMock(return_value=mock_client)
        mock_aclient.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_aclient):
            with pytest.raises(Exception, match="API down"):
                async for _ in handler.fetch_documents(request):
                    pass
