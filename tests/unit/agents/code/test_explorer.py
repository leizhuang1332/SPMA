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

    def test_reflection_fields_default_zero(self):
        """Task 1: reflection_count 与 consecutive_no_progress_reflections 默认 0。"""
        state = ExplorerState(query="test")
        assert state.reflection_count == 0
        assert state.consecutive_no_progress_reflections == 0


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


class MockRipgrepExecutorWithData(MockRipgrepExecutor):
    """带数据的 Mock RipgrepExecutor——可控返回。"""
    def __init__(self, glob_results=None, grep_results=None):
        self._glob = glob_results or []
        self._grep = grep_results or []

    async def glob_files(self, pattern, candidate_repos):
        return self._glob

    async def search(self, search_terms, candidate_repos, fallback_layer=0):
        return self._grep

    async def read_files(self, files):
        return [{"repo": f["repo"], "file_path": f["file_path"], "content": "mocked"} for f in files]


class MockASTParserWithExpansion:
    """Mock AST parser——调用时返回模拟 expanded context。"""
    def __init__(self, expand_results=None):
        self._expand = expand_results or []

    async def parse_file(self, path):
        return {}


@pytest.mark.anyio
class TestCodeExplorerRefineTerms:
    async def test_refine_terms_round1_uses_query_and_entities(self):
        """P3 对策：第 1 轮 expanded_context 为空时退化用 query + entities，不调 LLM。"""
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class TrackingLLM:
            def __init__(self):
                self.call_count = 0
            async def ainvoke(self, prompt):
                self.call_count += 1
                return None

        llm = TrackingLLM()
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6,
        )
        state = ExplorerState(
            round=1,
            query="用户登录",
            entities={"code_refs": ["auth.py"]},
        )
        await explorer._refine_terms(state)
        # LLM 不应被调用（首轮退化）
        assert llm.call_count == 0
        # search_terms 应包含 query + entities
        assert state.search_terms.get("query") == "用户登录"
        assert "auth.py" in state.search_terms.get("entities_code_refs", [])


@pytest.mark.anyio
class TestCodeExplorerStuckDetection:
    async def test_stuck_after_two_rounds_with_zero_new_files(self):
        """连续两轮 0 新文件 → stuck 收敛（round=2 触发）。"""
        executor = MockRipgrepExecutorWithData(
            glob_results=[], grep_results=[],  # 永远无新结果
        )
        ast = MockASTParserWithExpansion(expand_results=[])

        class NoOpLLM:
            async def ainvoke(self, prompt):
                # _refine_terms 用 query 退化，_assess 在 stuck 之前不需 LLM
                # 留作 None return 防御
                return None

        llm = NoOpLLM()
        round_events = []
        async def on_round(es):
            round_events.append((es.round, es.convergence.level if es.convergence else "pending"))

        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm,
            on_round_complete=on_round, max_rounds=6,
        )
        graph_state = {
            "entities": {"code_refs": []},
            "candidate_repos": ["repo_a"],
            "query": "test",
            "ripgrep_results": [],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        # round=2 时 stuck 触发
        assert "stuck" in result["convergence_reason"]
        assert result["rounds_used"] >= 2


@pytest.mark.anyio
class TestCodeExplorerCapReached:
    async def test_cap_reached_when_max_rounds_exceeded(self):
        """call_depth ≥ max_rounds 触发 cap_reached。"""
        executor = MockRipgrepExecutorWithData()  # 永远无新结果
        ast = MockASTParserWithExpansion()

        class NoOpLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "insufficient", "reason": "x"}')
                # 永不收敛，但会被 cap_reached 截断

        from unittest.mock import MagicMock
        llm = NoOpLLM()
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=2,
        )
        graph_state = {
            "entities": {"code_refs": []},
            "candidate_repos": ["repo_a"],
            "query": "test",
            "ripgrep_results": [],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        assert "cap_reached" in result["convergence_reason"]
        assert result["rounds_used"] <= 2


@pytest.mark.anyio
class TestCodeExplorerCallback:
    async def test_callback_invoked_after_each_round(self):
        """on_round_complete 在每轮结束触发。"""
        executor = MockRipgrepExecutorWithData(
            glob_results=[{"repo": "r", "file_path": "a.py"}],
        )
        ast = MockASTParserWithExpansion()

        class NoOpLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "sufficient", "reason": "x"}')

        from unittest.mock import MagicMock
        llm = NoOpLLM()
        call_count = {"n": 0}
        async def on_round(es):
            call_count["n"] += 1
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm,
            on_round_complete=on_round, max_rounds=3,
        )
        graph_state = {
            "entities": {"code_refs": ["a.py"]},
            "candidate_repos": ["r"],
            "query": "test",
            "ripgrep_results": [
                {"repo": "r", "file_path": "a.py", "line_number": 1, "match_text": "x"},
                {"repo": "r", "file_path": "a.py", "line_number": 2, "match_text": "y"},
                {"repo": "r", "file_path": "a.py", "line_number": 3, "match_text": "z"},
            ],
            "expanded_context": [],
        }
        await explorer.explore(graph_state)
        # 至少触发 1 次（可能更多）
        assert call_count["n"] >= 1
