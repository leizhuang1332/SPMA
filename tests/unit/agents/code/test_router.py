# tests/unit/agents/code/test_router.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.code.router import route_repos


class MockFilePathCache:
    def __init__(self, data=None):
        self.data = data or {}

    async def query_files(self, keyword, limit=5):
        results = []
        for repo, files in self.data.items():
            for f in files:
                if keyword.lower() in f.lower():
                    results.append({"repo_name": repo, "file_path": f})
                    if len(results) >= limit:
                        return results
        return results

    async def list_repos(self):
        return list(self.data.keys())


@pytest.mark.anyio
class TestRepoRouter:
    async def test_exact_file_match_via_code_refs(self):
        cache = MockFilePathCache({
            "backend": ["src/auth/oauth.py", "src/auth/token.py"],
            "frontend": ["src/components/Login.tsx"],
        })
        entities = {"code_refs": ["oauth.py"]}
        result = await route_repos(entities, cache)
        assert result["route_method"] == "exact_file_match"
        assert result["route_confidence"] == "high"
        assert "backend" in result["candidate_repos"]

    async def test_module_lookup_fallback(self):
        cache = MockFilePathCache({
            "backend": ["src/payment/checkout.py", "src/payment/billing.py"],
        })
        entities = {"code_refs": [], "module": "payment"}
        result = await route_repos(entities, cache)
        assert result["route_method"] == "module_lookup"

    async def test_broad_search_when_nothing_matches(self):
        cache = MockFilePathCache({
            "repo-a": ["README.md"],
            "repo-b": ["setup.py"],
            "repo-c": ["main.go"],
        })
        entities = {"code_refs": [], "module": "不存在的功能"}
        result = await route_repos(entities, cache, max_candidates=2)
        assert result["route_method"] == "broad_search"
        assert result["route_confidence"] == "low"
        assert len(result["candidate_repos"]) <= 2

    async def test_confidence_medium_when_many_candidates(self):
        """当匹配到超过3个仓库时，confidence 降为 medium"""
        cache = MockFilePathCache({
            "repo-a": ["src/module/feature.py"],
            "repo-b": ["lib/module/feature.py"],
            "repo-c": ["tests/module/feature.py"],
            "repo-d": ["docs/module/feature.py"],
        })
        entities = {"code_refs": ["feature.py"]}
        result = await route_repos(entities, cache)
        assert result["route_method"] == "exact_file_match"
        assert result["route_confidence"] == "medium"
        assert len(result["candidate_repos"]) == 4

    async def test_no_code_refs_no_module_falls_to_broad(self):
        cache = MockFilePathCache({
            "monorepo": ["src/main.py"],
        })
        entities = {"code_refs": [], "module": ""}
        result = await route_repos(entities, cache)
        assert result["route_method"] == "broad_search"
        assert result["route_confidence"] == "low"


@pytest.mark.anyio
class TestRouteReposQueryParam:
    async def test_query_param_is_optional_with_no_registry(self):
        """repo_registry=None 时，传 query 也不破坏旧行为。"""
        cache = MockFilePathCache({
            "repo-a": ["README.md"],
            "repo-b": ["setup.py"],
        })
        entities = {"code_refs": [], "module": ""}
        # 旧实现签名：route_repos(entities, cache) 也应工作
        result = await route_repos(
            query="支付接口的认证逻辑",  # 新参数
            entities=entities,
            file_path_cache=cache,
            repo_registry=None,  # 主路径禁用
            llm=None,
        )
        # 没有 repo_registry 时，行为完全兼容旧实现 → broad_search
        assert result["route_method"] == "broad_search"
        assert result["route_confidence"] == "low"


class MockRepoRegistry:
    """Mock RepoRegistry for Stage 0/1/2 测试。"""
    def __init__(self, repos, keyword_results=None):
        self._repos = repos
        self._keyword_results = keyword_results or []

    async def list_active_repos(self):
        return self._repos

    async def list_repos_by_keyword(self, keyword, top_k=20, similarity_threshold=0.3):
        return self._keyword_results

    async def get_repo_by_name(self, name):
        for r in self._repos:
            if r.repo_name == name:
                return r
        return None


def _make_repo(name, display="显示名", desc="描述", tags=None):
    """构造 RepoMeta dataclass 模拟对象。"""
    from spma.ingestion.code.repo_registry import RepoMeta
    return RepoMeta(
        repo_name=name,
        display_name=display,
        description=desc,
        tags=tags or [],
        repo_url=None,
        local_path=f"/repos/{name}",
        languages=["Python"],
        enabled=True,
    )


class MockLLMResponse:
    def __init__(self, content):
        self._content = content
    async def ainvoke(self, prompt):
        return MagicMock(content=self._content)


@pytest.mark.anyio
class TestRouteReposStage0Single:
    async def test_single_stage_llm_routes_correctly(self):
        """仓库数 ≤ 5 走单阶段 LLM，route_method=db_registry_match_single。"""
        repos = [
            _make_repo("repo_auth", desc="用户认证"),
            _make_repo("repo_payment", desc="支付服务"),
        ]
        reg = MockRepoRegistry(repos)
        llm = MockLLMResponse('{"repo_names": ["repo_auth"], "reason": "匹配"}')
        result = await route_repos(
            query="用户登录",
            entities={"code_refs": [], "module": ""},
            file_path_cache=MockFilePathCache({}),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,  # 2 ≤ 5 → 走单阶段
        )
        assert result["route_method"] == "db_registry_match_single"
        assert result["candidate_repos"] == ["repo_auth"]
        assert result["route_confidence"] == "high"
