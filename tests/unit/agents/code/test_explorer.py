"""Tests for CodeExplorer class (design-13 §3.5 + spec §4.5)."""
import pytest
from spma.agents.code.explorer import ExplorerState, CodeExplorer


class TestExplorerState:
    def test_default_values(self):
        state = ExplorerState()
        assert state.round == 0
        assert state.previous_new_files == 0
        assert state.new_files_this_round == 0
        assert state.search_terms == {}
        assert state.ripgrep_results == []
        assert state.expanded_context == []
        assert state.seen_files == set()
        assert state.fallback_layer == 0
        assert state.call_depth == 0
        assert state.convergence is None
        assert state.query == ""
        assert state.entities == {}
        assert state.candidate_repos == []

    def test_seen_files_initialized_as_set(self):
        """seen_files 必须是 set 而非 list（去重语义）。"""
        state = ExplorerState()
        state.seen_files.add(("repo_a", "file.py"))
        state.seen_files.add(("repo_a", "file.py"))  # 重复
        assert len(state.seen_files) == 1


class MockRipgrepExecutor:
    """Mock RipgrepExecutor——所有方法返回空。"""
    async def glob_files(self, pattern, candidate_repos):
        return []
    async def search(self, search_terms, candidate_repos, fallback_layer=0):
        return []
    async def read_files(self, files):
        return []


class MockASTParser:
    async def parse_file(self, path):
        return {}


class MockGraphState(dict):
    """Mock CodeAgentState——简单 dict 即可（explore() 写回字段）。"""
    pass


@pytest.mark.anyio
class TestCodeExplorerExplore:
    async def test_explore_terminates_on_goal_verified(self):
        """第 1 轮 goal_verified 后立即退出（不进入第 2 轮）。"""
        executor = MockRipgrepExecutor()
        ast = MockASTParser()

        class VerifyingLLM:
            """不在 _refine_terms 时被调用，_assess 也不需要 LLM（直接 goal_verified 收敛）。"""
            async def ainvoke(self, prompt):
                return None  # 不应被调用

        explorer = CodeExplorer(
            ripgrep_executor=executor,
            ast_parser=ast,
            llm=VerifyingLLM(),
            max_rounds=6,
        )
        # 构造 graph state：含 3 个 ripgrep_results + code_refs
        graph_state = {
            "entities": {"code_refs": ["auth.py"]},
            "candidate_repos": ["repo_auth"],
            "query": "用户登录",
            "ripgrep_results": [
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 1, "match_text": "def login"},
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 5, "match_text": "def logout"},
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 10, "match_text": "def register"},
            ],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        assert result["convergence_reason"] == "goal_verified:deterministic_code_refs"
        # round 应为 1（首轮即收敛）
        assert result["rounds_used"] == 1
