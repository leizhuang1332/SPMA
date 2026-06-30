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


@pytest.mark.anyio
class TestRepoRegistryGetByName:
    async def test_get_repo_by_name_hit(self):
        rows = [
            {
                "repo_name": "repo_auth",
                "display_name": "用户认证",
                "description": "认证服务",
                "tags": ["auth"],
                "repo_url": None,
                "local_path": "/repos/repo_auth",
                "languages": ["Python"],
                "enabled": True,
            }
        ]
        pool = _make_pool_with_rows_for_one(rows[0])
        reg = RepoRegistry(pool, optional=True)
        result = await reg.get_repo_by_name("repo_auth")
        assert result is not None
        assert result.repo_name == "repo_auth"


def _make_pool_with_rows_for_one(row):
    """构造 mock pool，fetchrow() 返回单行；fetch() 用于启动校验 0 行。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=1)  # 启动期校验通过
    pool.acquire = MagicMock(return_value=conn)
    return pool


def _make_pool_with_keyword_results(initial_rows, relaxed_rows, fallback_rows):
    """构造 mock pool：首次 fetch 返回 initial_rows；relaxed 后返回 relaxed_rows；fallback 全表返回 fallback_rows。

    RepoRegistry.list_repos_by_keyword 内部应尝试 0.3 → 0.15 → fallback；
    本 helper 模拟 '连续尝试'：根据每次 fetch 调用返回不同结果。
    """
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    # fetch() 按调用次序返回：第一次 initial_rows，第二次 relaxed_rows，第三次 fallback_rows
    fetch_responses = [initial_rows, relaxed_rows, fallback_rows]
    fetch_idx = {"i": 0}

    async def fetch_side_effect(*args, **kwargs):
        result = fetch_responses[fetch_idx["i"]] if fetch_idx["i"] < len(fetch_responses) else fallback_rows
        fetch_idx["i"] += 1
        return result

    conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.anyio
class TestRepoRegistryListByKeyword:
    def _row(self, name, **overrides):
        base = {
            "repo_name": name,
            "display_name": f"display_{name}",
            "description": f"description_{name}",
            "tags": ["tag_a", "tag_b"],
            "repo_url": None,
            "local_path": f"/repos/{name}",
            "languages": ["Python"],
            "enabled": True,
        }
        base.update(overrides)
        return base

    async def test_keyword_match_chinese(self):
        """中文 keyword 在 description 字段命中。"""
        rows = [self._row("repo_auth", description="用户认证服务")]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("认证", top_k=20)
        assert len(result) == 1
        assert result[0].repo_name == "repo_auth"

    async def test_keyword_match_english(self):
        """英文 keyword 在 repo_name 字段命中。"""
        rows = [self._row("repo_auth", repo_name="repo_auth")]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("auth", top_k=20)
        assert len(result) == 1

    async def test_keyword_match_tags_exact(self):
        """tags 数组精确命中（不受阈值影响）。"""
        rows = [self._row("repo_payment", tags=["支付", "payment"])]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("支付", top_k=20)
        assert len(result) == 1
        assert result[0].repo_name == "repo_payment"

    async def test_keyword_threshold_relaxation(self):
        """召回 < 3 时阈值自动放宽 0.3 → 0.15 重试。"""
        # 首次 fetch 返回 1 条（< 3 触发松弛）
        initial = [self._row("repo_auth")]
        # 松弛后返回 5 条
        relaxed = [self._row(f"repo_{i}") for i in range(5)]
        pool = _make_pool_with_keyword_results(initial, relaxed, [])
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("模糊关键词", top_k=20)
        assert len(result) == 5  # 用松弛后的结果

    async def test_keyword_empty_query_returns_fallback(self):
        """空查询：兜底全表 ORDER BY id LIMIT top_k。"""
        fallback = [self._row(f"repo_{i}") for i in range(3)]
        # 空 query 时 0.3 和 0.15 都返回空，最终触发全表兜底
        pool = _make_pool_with_keyword_results([], [], fallback)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("", top_k=20)
        assert len(result) == 3
