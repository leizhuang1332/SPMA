"""Tests for CodeExplorer class (design-13 §3.5 + spec §4.5)."""
import asyncio
from unittest.mock import AsyncMock

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


# ============================================================
# Task 3: _reflect_and_replan 反思层测试
# ============================================================

from spma.agents.code.completeness import CodeCompletenessResult  # noqa: E402


@pytest.fixture
def fake_repo_whitelist():
    """Task 3 测试用 repo 白名单——直接使用 frozenset 而非 FakeRegistry。"""
    return frozenset({"core", "auth-svc"})


@pytest.fixture
def make_state():
    """构造一个标准 ExplorerState——search_terms 含 module=auth, candidate_repos=[core]。"""
    def _make(round_num: int = 2):
        return ExplorerState(
            round=round_num,
            query="q",
            search_terms={"module": ["auth"]},
            candidate_repos=["core"],
            convergence=CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
            ),
        )
    return _make


@pytest.mark.anyio
class TestReflectAndReplan:
    async def test_reflect_and_replan_continues_on_llm_timeout(
        self, make_state, fake_repo_whitelist,
    ):
        """LLM 超时时，反思应被跳过，state 不被修改。"""
        from unittest.mock import AsyncMock as _AM

        llm = _AM()
        llm.ainvoke.side_effect = asyncio.TimeoutError("LLM timeout")

        explorer = CodeExplorer(
            ripgrep_executor=_AM(),
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )
        state = make_state(round_num=2)

        # 不应抛错
        await explorer._reflect_and_replan(state)

        # state.search_terms 未变
        assert state.search_terms == {"module": ["auth"]}
        # reflection_count 未增
        assert state.reflection_count == 0

    async def test_reflect_and_replan_continues_on_json_parse_failure(
        self, make_state, fake_repo_whitelist,
    ):
        """LLM 返回非 JSON 时，反思应被跳过。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage

        llm = _AM()
        llm.ainvoke.return_value = AIMessage(content="this is not json at all")

        explorer = CodeExplorer(
            ripgrep_executor=_AM(),
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )
        state = make_state(round_num=2)

        await explorer._reflect_and_replan(state)

        # state.search_terms 未变
        assert state.search_terms == {"module": ["auth"]}
        assert state.reflection_count == 0

    async def test_reflect_and_replan_updates_search_terms(
        self, make_state, fake_repo_whitelist,
    ):
        """正常 LLM 响应应被解析并应用到 state。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage

        llm = _AM()
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["authorization"]}, '
                '"drop_terms": [], "add_repos": [], '
                '"reasoning": "missing authz"}'
            )
        )

        explorer = CodeExplorer(
            ripgrep_executor=_AM(),
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )
        state = make_state(round_num=2)

        await explorer._reflect_and_replan(state)

        # authorization 被合并
        assert "authorization" in state.search_terms["module"]
        # reflection_count += 1
        assert state.reflection_count == 1


# ============================================================
# Task 4: _run_one_round 反思触发 + cap 机制
# ============================================================


@pytest.mark.anyio
class TestRunOneRoundReflectionTrigger:
    async def test_run_one_round_triggers_reflection_when_should_reflect_true(
        self, fake_repo_whitelist,
    ):
        """_run_one_round 在 _assess 返回 should_reflect=True 时应调 _reflect_and_replan。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage

        llm = _AM()
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["authorization"]}, '
                '"drop_terms": [], "add_repos": [], "reasoning": "ok"}'
            )
        )

        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []

        ast_parser = _AM()

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=ast_parser,
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )

        state = ExplorerState(
            round=1,
            query="how does auth work?",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=2,
            previous_new_files=10,
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        await explorer._run_one_round(state)

        # 反思被触发：reflection_count += 1
        assert state.reflection_count == 1
        # new_search_terms 被合并
        assert "authorization" in state.search_terms["module"]

    async def test_run_one_round_skips_reflection_on_stuck(
        self, fake_repo_whitelist,
    ):
        """_assess 判定 stuck（should_reflect=False）时不应触发反思。"""
        from unittest.mock import AsyncMock as _AM

        llm = _AM()
        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        state = ExplorerState(
            round=1,
            query="q",
            search_terms={"module": ["x"]},
            candidate_repos=["core"],
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="stuck",
                reason="no_progress_2_rounds",
                level="L1",
                should_reflect=False,
            )

        explorer._assess = fake_assess

        await explorer._run_one_round(state)

        # 反思未被触发
        assert state.reflection_count == 0

    async def test_run_one_round_skips_reflection_on_goal_verified(
        self, fake_repo_whitelist,
    ):
        """_assess 判定 goal_verified（should_reflect=False）时不应触发反思（已收敛）。"""
        from unittest.mock import AsyncMock as _AM

        llm = _AM()
        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        state = ExplorerState(
            round=1,
            query="q",
            search_terms={"module": ["x"]},
            candidate_repos=["core"],
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="converge",
                reason="goal_verified",
                level="L1",
                should_reflect=False,
            )

        explorer._assess = fake_assess

        await explorer._run_one_round(state)

        # 反思未被触发
        assert state.reflection_count == 0


@pytest.mark.anyio
class TestRunOneRoundCapMechanisms:
    async def test_run_one_round_caps_after_2_no_progress_reflections(
        self, fake_repo_whitelist,
    ):
        """连续 2 次反思后 new_files_this_round==0 应触发 cap。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage

        llm = _AM()
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["auth"]}, '
                '"drop_terms": [], "add_repos": [], "reasoning": "ok"}'
            )
        )

        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )

        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=0,  # 反思后无新增
            previous_new_files=0,
            consecutive_no_progress_reflections=1,  # 已累计 1 次
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        await explorer._run_one_round(state)

        # 应被强制 cap
        assert state.convergence.verdict == "cap"
        assert state.convergence.reason == "reflection_no_progress"
        assert state.consecutive_no_progress_reflections == 2

    async def test_run_one_round_caps_on_empty_reflected_terms(
        self, fake_repo_whitelist,
    ):
        """反思后 search_terms 全空应强制 cap。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage

        llm = _AM()
        # 反思 LLM 返回空 search_terms 并 drop 原 auth
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {}, '
                '"drop_terms": ["auth"], "add_repos": [], '
                '"reasoning": "give up"}'
            )
        )

        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},  # 只有 auth
            new_files_this_round=2,
            previous_new_files=5,
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        await explorer._run_one_round(state)

        # 应被强制 cap（empty terms）
        assert state.convergence.verdict == "cap"
        assert state.convergence.reason == "reflection_empty_terms"
