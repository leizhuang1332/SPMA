"""Tests for RepoRegistry class (design-13 §3.2)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.ingestion.code.repo_registry import RepoMeta, RepoRegistry


def _make_pool_with_rows(rows):
    """构造一个 mock asyncpg.Pool，fetch() 返回 rows。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=rows)
    pool.acquire = MagicMock(return_value=conn)
    return pool


def _make_pool_with_count(count: int):
    """构造 mock pool，fetchval() 返回 count（启动期校验用）。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=count)
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.anyio
class TestRepoRegistryListActive:
    async def test_list_active_repos_returns_repo_metas(self):
        rows = [
            {
                "repo_name": "repo_auth",
                "display_name": "用户认证",
                "description": "认证服务",
                "tags": ["auth", "认证"],
                "repo_url": "https://example.com/auth",
                "local_path": "/repos/repo_auth",
                "languages": ["Python"],
                "enabled": True,
            }
        ]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_active_repos()
        assert len(result) == 1
        assert result[0].repo_name == "repo_auth"
        assert result[0].display_name == "用户认证"
        assert result[0].tags == ["auth", "认证"]
