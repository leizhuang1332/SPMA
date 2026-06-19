"""摄入 API 端到端冒烟测试。

测试 HTTP 层面行为：路由注册、认证要求、输入验证、健康检查。
使用 mock IngestionController 隔离后端依赖。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_headers():
    return {"Authorization": "Bearer spma-admin-dev-key"}


@pytest.fixture
def user_headers():
    return {"Authorization": "Bearer spma-user-dev-key"}


@pytest.fixture(autouse=True)
def _reset_ingestion_controller():
    """每个测试后重置 IngestionController 单例，避免状态泄漏。"""
    from spma.api import dependencies as dep
    dep._ingestion_controller = None
    yield
    dep._ingestion_controller = None


@pytest.fixture
def mock_controller():
    """用 mock 设置 IngestionController 单例。"""
    from spma.api import dependencies as dep
    mock = AsyncMock()
    mock.ingest_documents = AsyncMock(
        return_value={"pipeline_run_id": "mock-run-001", "status": "running", "mode": "incremental", "source": "confluence"}
    )
    mock.ingest_code = AsyncMock(
        return_value={"pipeline_run_id": "mock-run-002", "status": "running"}
    )
    mock.ingest_schema = AsyncMock(
        return_value={"pipeline_run_id": "mock-run-003", "status": "running"}
    )
    mock.get_pipeline_status = AsyncMock(return_value={})
    mock.get_pipeline_run = AsyncMock(return_value=None)
    mock.get_freshness = AsyncMock(return_value={})
    mock.refresh_synonym_map = AsyncMock(return_value=5)
    mock.query_synonym_map = AsyncMock(return_value={"total": 0, "entries": []})
    mock.handle_confluence_webhook = AsyncMock(return_value={"status": "ok"})
    mock.handle_git_webhook = AsyncMock(return_value={"status": "ok"})

    dep.set_ingestion_controller(mock)
    return mock


class TestIngestionEndpoints:
    @pytest.fixture
    def client(self, mock_controller):
        from spma.api.app import create_app
        app = create_app()
        return TestClient(app)

    # ── 输入验证 (§3-5) ──

    def test_ingest_documents_validates_input(self, client, admin_headers):
        """POST /api/v1/ingest/documents 对无效 mode 返回 422。"""
        resp = client.post(
            "/api/v1/ingest/documents",
            json={"source": "confluence", "mode": "invalid"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_ingest_documents_valid_request(self, client, admin_headers, mock_controller):
        """POST /api/v1/ingest/documents 合法请求返回 200 含 pipeline_run_id。"""
        resp = client.post(
            "/api/v1/ingest/documents",
            json={"source": "confluence", "mode": "incremental"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_run_id" in data
        assert data["status"] == "running"

    def test_ingest_code_validates_input(self, client, admin_headers):
        """POST /api/v1/ingest/code 对无效 mode 返回 422。"""
        resp = client.post(
            "/api/v1/ingest/code",
            json={"mode": "invalid"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_ingest_schema_validates_input(self, client, admin_headers):
        """POST /api/v1/ingest/schema 对无效 mode 返回 422。"""
        resp = client.post(
            "/api/v1/ingest/schema",
            json={"mode": "invalid"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    # ── 认证要求 (§6-7, §10) ──

    def test_get_ingest_status_requires_auth(self, client):
        """GET /api/v1/ingest/status 无认证返回 401（HTTPBearer 缺 header）。"""
        resp = client.get("/api/v1/ingest/status")
        assert resp.status_code == 401

    def test_get_freshness_requires_auth(self, client):
        """GET /api/v1/ingest/freshness 无认证返回 401。"""
        resp = client.get("/api/v1/ingest/freshness")
        assert resp.status_code == 401

    def test_synonym_map_endpoints_require_auth(self, client):
        """/ingest/synonym-map GET 和 refresh POST 无认证返回 401。"""
        resp_get = client.get("/api/v1/ingest/synonym-map")
        resp_post = client.post("/api/v1/ingest/synonym-map/refresh", json={})
        assert resp_get.status_code == 401
        assert resp_post.status_code == 401

    # ── Webhook 端点 (§8) ──

    def test_webhook_confluence_accepts_post(self, client):
        """POST /api/v1/webhooks/confluence 接受合法 POST（无 secret 时放行）。"""
        resp = client.post(
            "/api/v1/webhooks/confluence",
            json={"page_id": "12345", "version": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_webhook_confluence_missing_page_id(self, client):
        """POST /api/v1/webhooks/confluence 缺少 page_id 返回 400。"""
        resp = client.post(
            "/api/v1/webhooks/confluence",
            json={"version": 1},
        )
        assert resp.status_code == 400

    def test_webhook_git_accepts_post(self, client):
        """POST /api/v1/webhooks/git 接受合法 POST（无 secret 时放行）。"""
        with patch("spma.api.routes.ingestion_webhooks.GitManager") as mock_git_cls:
            mock_git = AsyncMock()
            mock_git.handle_webhook = AsyncMock(
                return_value={
                    "repo_name": "test",
                    "branch": "main",
                    "changed_files": ["README.md"],
                }
            )
            mock_git_cls.return_value = mock_git

            resp = client.post(
                "/api/v1/webhooks/git",
                json={
                    "repository": {"name": "test"},
                    "ref": "refs/heads/main",
                    "commits": [],
                },
            )
            assert resp.status_code == 200

    # ── 健康检查 ──

    def test_health_endpoint_works(self, client):
        """GET /health 返回健康检查结果。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestIngestDocumentsMarkdownDir:
    """E2E tests for POST /api/v1/ingest/documents with markdown_dir source."""

    @pytest.fixture
    def client(self, mock_controller):
        from spma.api.app import create_app
        app = create_app()
        return TestClient(app)

    def test_markdown_dir_valid_request(self, client, admin_headers, mock_controller):
        """Valid markdown_dir request returns 200 with pipeline_run_id."""
        from spma.api.schemas.ingestion import IngestionResponse

        mock_controller.ingest_documents.return_value = IngestionResponse(
            pipeline_run_id="ingest-doc-test-markdown-001",
            source="markdown_dir",
            mode="full",
            status="running",
        )

        payload = {
            "source": "markdown_dir",
            "mode": "full",
            "path": "/data/docs/**/*.md",
        }

        response = client.post(
            "/api/v1/ingest/documents",
            json=payload,
            headers=admin_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "markdown_dir"
        assert data["mode"] == "full"

    def test_markdown_dir_without_path_uses_config_fallback(self, client, admin_headers, mock_controller):
        """Request without path should still be accepted (falls back to config)."""
        from spma.api.schemas.ingestion import IngestionResponse

        mock_controller.ingest_documents.return_value = IngestionResponse(
            pipeline_run_id="ingest-doc-test-fallback-001",
            source="markdown_dir",
            mode="incremental",
            status="running",
        )

        payload = {
            "source": "markdown_dir",
            "mode": "incremental",
        }

        response = client.post(
            "/api/v1/ingest/documents",
            json=payload,
            headers=admin_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "markdown_dir"
