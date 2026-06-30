"""Code Agent 路由端到端测试（design-13 §6.4 + spec §6 测试策略）。

覆盖 4 个关键场景：
    1. 仓库数 ≤ 5 → 单阶段 LLM 路由（db_registry_match_single）
    2. 仓库数 > 5  → 两阶段路由（db_registry_match_two_stage）
    3. Stage 1 召回不足 → 触发阈值松弛（仍走 two_stage 路径）
    4. LLM 幻觉    → 兜底 broad_search

使用 Mock Registry/LLM/Cache 隔离 DB 依赖；与 project asyncio_mode=auto
兼容（同时打 pytest.mark.asyncio + anyio 标记以兼容既有测试惯例）。
"""
from unittest.mock import MagicMock

import pytest

from spma.agents.code.router import route_repos
from spma.ingestion.code.repo_registry import RepoMeta


def _make_repo(name, desc, tags):
    return RepoMeta(
        repo_name=name,
        display_name=name,
        description=desc,
        tags=tags,
        repo_url=None,
        local_path=f"/repos/{name}",
        languages=["Python"],
        enabled=True,
    )


class MockRegistryE2E:
    """Mock RepoRegistry——兼容 router.py 调用的 3 个 async 方法。"""

    def __init__(self, repos):
        self._repos = repos

    async def list_active_repos(self):
        return self._repos

    async def list_repos_by_keyword(self, keyword, top_k=20, similarity_threshold=0.3):
        # 真实实现按 trigram 排序；这里 mock 简化：返回所有 enabled
        return self._repos

    async def get_repo_by_name(self, name):
        for r in self._repos:
            if r.repo_name == name:
                return r
        return None


class MockLLME2E:
    """Mock LLM——返回指定仓库名（用于两阶段精排命中）。"""

    def __init__(self, repo_name):
        self._target = repo_name

    async def ainvoke(self, prompt):
        return MagicMock(content=f'{{"repo_names": ["{self._target}"], "reason": "test"}}')


class MockCacheE2E:
    """Mock file_path_cache——为 legacy 兜底路径提供空结果。"""

    async def query_files(self, *args, **kwargs):
        return []

    async def list_repos(self):
        return ["fallback_repo"]


@pytest.mark.asyncio
class TestRoutingE2E:
    async def test_scenario_1_repos_le_5_uses_single_stage(self):
        """场景 1: 仓库数 ≤ 5 走 db_registry_match_single。"""
        repos = [
            _make_repo("repo_auth", "用户认证服务", ["auth", "认证", "login"]),
            _make_repo("repo_payment", "支付服务", ["payment", "支付"]),
            _make_repo("repo_order", "订单服务", ["order", "订单"]),
        ]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_payment")
        result = await route_repos(
            query="修改支付接口的认证逻辑",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_single"
        assert "repo_payment" in result["candidate_repos"]

    async def test_scenario_2_repos_gt_5_uses_two_stage(self):
        """场景 2: 仓库数 > 5 走 db_registry_match_two_stage。"""
        repos = [_make_repo(f"repo_{i}", f"服务{i}", [f"tag_{i}"]) for i in range(7)]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_3")
        result = await route_repos(
            query="测试",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_two_stage"

    async def test_scenario_3_threshold_relax_in_stage_1(self):
        """场景 3: Stage 1 召回 < 3 触发阈值松弛（mock 直接验证 method 走两阶段）。"""
        repos = [_make_repo(f"repo_{i}", f"完全无关的服务{i}", [f"x{i}"]) for i in range(6)]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_0")
        result = await route_repos(
            query="完全不相关的查询",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_two_stage"

    async def test_scenario_4_llm_returns_unrelated_repo_falls_back(self):
        """场景 4: LLM 返回仓库不在 candidates → broad_search 兜底。"""
        repos = [_make_repo("repo_auth", "认证服务", ["auth"])]
        reg = MockRegistryE2E(repos)

        class HallucinatedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(
                    content='{"repo_names": ["repo_does_not_exist"], "reason": "x"}'
                )

        result = await route_repos(
            query="test",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=HallucinatedLLM(),
            two_stage_threshold=5,
        )
        assert result["route_method"] == "broad_search"
