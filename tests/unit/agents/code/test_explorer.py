"""Tests for CodeExplorer class (design-13 §3.5 + spec §4.5)."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from spma.agents.code.explorer import ExplorerState, CodeExplorer


# ============================================================
# Task 3：REFINE_TERMS_PROMPT 模板测试
# ============================================================


class TestRefineTermsPrompt:
    """spec §3.1 #5：prompts.py 加 REFINE_TERMS_PROMPT 模板，要求 LLM 输出 glob_patterns。"""

    def test_prompt_contains_glob_patterns_keyword(self):
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        assert "glob_patterns" in REFINE_TERMS_PROMPT

    def test_prompt_contains_json_schema(self):
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        # 模板应明确要求输出 JSON 且含 4 个字段
        for key in ("exact_terms", "fuzzy_terms", "tag_terms", "glob_patterns"):
            assert key in REFINE_TERMS_PROMPT, f"missing key: {key}"

    def test_prompt_instructs_language_inference(self):
        """prompt 必须告诉 LLM 按用户意图/语言推断 glob。"""
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        # 中英文指令都要含
        assert any(kw in REFINE_TERMS_PROMPT.lower() for kw in ["language", "语言", "意图", "intent", "file type", "文件类型"])


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

    def test_glob_patterns_resolved_default_empty(self):
        """spec §2.2：glob_patterns_resolved 默认空字符串，运行时由 _refine_terms 写入。"""
        state = ExplorerState()
        assert state.glob_patterns_resolved == ""


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

    async def test_refine_llm_returns_valid_globs(self):
        """LLM 返回合法 glob_patterns → resolved=llm，原样保留。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content=(
                    '{"exact_terms": ["x"], "fuzzy_terms": [], '
                    '"tag_terms": [], '
                    '"glob_patterns": ["**/*.java", "**/test/*.java"]}'
                ))

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2,  # 后续轮
            query="Java 服务",
            expanded_context=[{"repo": "r", "file_path": "old.java"}],  # 非空 → 走 LLM
            search_terms={"query": "x", "exact_terms": ["old"]},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.java", "**/test/*.java"]
        assert state.glob_patterns_resolved == "llm"

    async def test_refine_llm_partial_invalid_keeps_valid(self):
        """LLM 返回混合（合法+非法）→ 仅保留合法部分，resolved 仍 = llm。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content=(
                    '{"glob_patterns": ["**/*.java", ";rm", "../x", ""]}'
                ))

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="x",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.java"]
        assert state.glob_patterns_resolved == "llm"

    async def test_refine_llm_all_invalid_query_has_ext_uses_query_fallback(self):
        """LLM 全非法 + query 含扩展名 → 用 query 词法，resolved=fallback_query。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"glob_patterns": ["../bad", ";"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="改 pom.xml 依赖",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.xml"]
        assert state.glob_patterns_resolved == "fallback_query"

    async def test_refine_llm_all_invalid_query_no_ext_uses_wildcard(self):
        """LLM 全非法 + query 无扩展名 → 用 **/*.*，resolved=fallback_wildcard。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"glob_patterns": ["../bad"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="查订单服务",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.*"]
        assert state.glob_patterns_resolved == "fallback_wildcard"

    async def test_refine_llm_missing_glob_patterns_field(self):
        """LLM JSON 缺 glob_patterns 键 → 走降级链。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"exact_terms": ["x"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="改 pom.xml 依赖",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.xml"]
        assert state.glob_patterns_resolved == "fallback_query"

    async def test_refine_llm_exception_preserves_search_terms_and_resolves_wildcard(self):
        """LLM 抛异常时保持 search_terms 不变（保留 cap 机制判定），仅设 resolved=fallback_wildcard。

        关键不变量：spec §4.2 Trace 2 + 反射层 cap 机制 (explorer.py:152-154) 依赖
        'if not state.search_terms or all(not terms for terms in state.search_terms.values())'。
        如果 except 路径给 search_terms 写 ['**/*.*']，cap 永远不触发，破坏 7 mode 收敛保证。
        因此本测试的正确语义是：
        - state.search_terms 保持 LLM 调用前的值（不修改）
        - state.glob_patterns_resolved == 'fallback_wildcard'（让下游 _glob 走全仓库扫描）
        """
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FailingLLM:
            async def ainvoke(self, prompt):
                raise TimeoutError("LLM timeout")

        llm = FailingLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        # 构造一个 non-empty pre-existing search_terms（确保 except 路径不应改它）
        initial_terms = {"exact_terms": ["old"]}
        state = ExplorerState(
            round=2, query="查订单服务",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms=dict(initial_terms),  # copy to detect mutation
        )
        await explorer._refine_terms(state)
        # 1. search_terms 必须保持 LLM 调用前的值（关键不变量）
        assert state.search_terms == initial_terms
        # 2. glob_patterns_resolved 必须被设为 fallback_wildcard
        assert state.glob_patterns_resolved == "fallback_wildcard"
        # 3. 注意：search_terms 中没有 glob_patterns 键（保持上轮）
        assert "glob_patterns" not in state.search_terms


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


# ============================================================
# Task 4 review fix（C1 + I4）：explore() 循环必须识别 cap verdict
# ============================================================


@pytest.mark.anyio
class TestExploreLoopTerminatesOnCap:
    """验证 explore() 主循环在 verdict="cap" 时真正停止（C1 修复）。

    之前 _is_converged 只识别 "converge"，导致反思触发的 cap（empty_terms /
    no_progress）在下一轮仍会跑，浪费 LLM/ripgrep 调用。这些测试直接覆盖
    explore() 完整循环，验证 cap 后 rounds_used 不会超过触发 cap 的那一轮。
    """

    async def test_explore_loop_terminates_when_run_one_round_sets_cap(
        self, fake_repo_whitelist,
    ):
        """直接 mock _run_one_round 让其设 verdict="cap"，验证 _is_converged 修复。"""
        from unittest.mock import AsyncMock as _AM
        from spma.agents.code.completeness import CodeCompletenessResult

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
            max_rounds=6,
        )

        # 计数 + mock _run_one_round：第一次调用设 cap，第二次不应被调用
        run_count = {"n": 0}

        async def fake_run_one_round(state):
            run_count["n"] += 1
            state.round += 1
            state.convergence = CodeCompletenessResult(
                verdict="cap",
                reason="reflection_empty_terms",
                level="L1",
            )

        explorer._run_one_round = fake_run_one_round

        graph_state = {
            "query": "q",
            "entities": {"module": ["auth"]},
            "candidate_repos": ["core"],
            "ripgrep_results": [],
            "expanded_context": [],
            "fallback_layer": 0,
            "call_depth": 0,
        }

        result = await explorer.explore(graph_state)

        # 关键断言（C1 修复点）
        assert run_count["n"] == 1, (
            f"explore() 必须在 verdict='cap' 后停止，但跑了 {run_count['n']} 轮"
        )
        assert result["rounds_used"] == 1
        assert result["convergence_reason"].endswith("reflection_empty_terms"), (
            f"应被 reflection_empty_terms cap，实际: {result['convergence_reason']}"
        )

    async def test_explore_loop_terminates_after_2_no_progress_reflections(
        self, fake_repo_whitelist,
    ):
        """完整链路：连续 2 次反思无新增 → explore() 必须停止（C1 + C2 联合验证）。

        关键点：mock _assess 让它返回 diminishing_returns + should_reflect=True，
        这样反思链路被触发；同时 _read 不增加新文件（ripgrep.read_files=[]），
        使 consecutive_no_progress_reflections 累计到 2。
        """
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage
        from spma.agents.code.completeness import CodeCompletenessResult

        # 反思 LLM 返回合理的新 search_terms（不会触发 empty_terms cap）
        llm = _AM()
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["authorization"]}, '
                '"drop_terms": [], "add_repos": [], "reasoning": "trying synonyms"}'
            )
        )
        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []  # 关键：read_files 永远返回空

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=6,
        )

        # mock _assess 让它稳定返回 diminishing_returns + should_reflect=True
        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        # 构造 graph_state：previous_new_files > 0，让 _assess mock 的
        # diminishing_returns 看起来合理（不依赖真实 _read 链路）
        graph_state = {
            "query": "q",
            "entities": {"module": ["auth"]},
            "candidate_repos": ["core"],
            "ripgrep_results": [],
            "expanded_context": [],
            "fallback_layer": 0,
            "call_depth": 0,
        }

        result = await explorer.explore(graph_state)

        # 关键断言（C1 + C2 联合修复点）
        # 第 1 轮：diminishing_returns + new_files=0 → consecutive_no_progress = 1
        # 第 2 轮：再次 diminishing_returns + new_files=0 → consecutive = 2 → cap
        assert result["rounds_used"] == 2, (
            f"应在 2 轮反思无进展后 cap，实际跑了 {result['rounds_used']} 轮"
        )
        assert result["convergence_reason"].endswith("reflection_no_progress"), (
            f"应被 reflection_no_progress cap，实际: {result['convergence_reason']}"
        )


# ============================================================
# Task 6：e2e fixture — 验证反思触发 → search_terms 修改 → 下一轮生效
# + 埋点真触发测试（Task 5 review I-1 follow-up）
# ============================================================


@pytest.mark.anyio
class TestExploreEndToEndWithReflection:
    """Task 6 e2e fixture：mock LLM 触发反思 → 验证 search_terms 变更 → 下一轮生效。"""

    async def test_e2e_reflection_path_triggers_replan(self, fake_repo_whitelist):
        """端到端：diminishing_returns 触发反思 → LLM 重写 search_terms → 下一轮 _assess 看到新 terms。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage
        from spma.agents.code.completeness import CodeCompletenessResult

        # 反思 LLM：返回新搜索词
        reflect_llm_response = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["authorization"]}, '
                '"drop_terms": [], "add_repos": [], "reasoning": "extend to synonyms"}'
            )
        )
        # 后续 refine 调用的 LLM（不重要，内容任意）
        refine_llm_response = AIMessage(content='{"exact_terms": ["authorization"], "fuzzy_terms": []}')

        llm = _AM()
        llm.ainvoke.side_effect = [reflect_llm_response, refine_llm_response, refine_llm_response]

        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []  # new_files_this_round 始终为 0

        # 用 on_round_complete 回调捕获每轮后的 state
        captured_states = []

        async def capture_state(s):
            captured_states.append({
                "round": s.round,
                "search_terms": dict(s.search_terms),
                "reflection_count": s.reflection_count,
            })

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
            max_rounds=4,
            on_round_complete=capture_state,
        )

        # 第 1 轮 _assess 返回 diminishing_returns（触发反思）
        # 第 2 轮 _assess 返回 should_reflect=False（不反思）+ new_files > 0（避免 cap）
        # 第 3 轮 _assess 返回 verdict="converge"（让 explore() 退出，避免死循环）
        assess_call = {"n": 0}
        original_assess = explorer._assess

        async def fake_assess(s):
            assess_call["n"] += 1
            if assess_call["n"] == 1:
                s.convergence = CodeCompletenessResult(
                    verdict="progress",
                    reason="diminishing_returns",
                    level="L1",
                    should_reflect=True,
                )
            elif assess_call["n"] == 2:
                s.convergence = CodeCompletenessResult(
                    verdict="progress",
                    reason="progress",
                    level="L1",
                    should_reflect=False,
                )
                # 模拟第 2 轮 read 新增文件（避免 cap）
                s.new_files_this_round = 3
                s.previous_new_files = s.new_files_this_round
            else:
                # 第 3 轮及以后：返回 converge 让 explore() 退出
                s.convergence = CodeCompletenessResult(
                    verdict="converge",
                    reason="goal_verified",
                    level="L1",
                    should_reflect=False,
                )

        explorer._assess = fake_assess

        # 跑 explore() 完整循环
        graph_state = {
            "query": "how does auth work?",
            "entities": {"module": ["auth"]},
            "candidate_repos": ["core"],
            "ripgrep_results": [],
            "expanded_context": [],
            "fallback_layer": 0,
            "call_depth": 0,
        }

        result = await explorer.explore(graph_state)

        # 关键断言：反思真的触发了且 search_terms 被改写
        # 第 1 轮：触发反思（diminishing_returns）
        # 第 2 轮：should_reflect=False，无反思，新增文件
        # 第 3 轮：返回 converge 退出
        assert result["rounds_used"] == 3
        assert assess_call["n"] == 3  # 跑了 3 轮
        assert result["convergence_reason"].endswith("goal_verified")

        # Task 6 review I-2: 验证反思真的改写了 search_terms
        # on_round_complete 回调在每轮结束后被调用，captured_states 包含 3 轮快照
        assert len(captured_states) == 3, (
            f"on_round_complete 应在 3 轮后调用 3 次，实际: {len(captured_states)}"
        )
        # 第 1 轮：触发反思前
        assert captured_states[0]["round"] == 1
        # 第 2 轮：反思已生效，reflection_count 应 >= 1 且 search_terms 应包含 "authorization"
        assert captured_states[1]["round"] == 2
        assert captured_states[1]["reflection_count"] >= 1, (
            f"第 2 轮 reflection_count 应 >= 1，实际: {captured_states[1]['reflection_count']}"
        )
        # 验证反思后 search_terms["module"] 包含 LLM 返回的 "authorization"
        round2_terms = captured_states[1]["search_terms"]
        module_terms = round2_terms.get("module", [])
        assert "authorization" in module_terms, (
            f"反思后 search_terms.module 应包含 'authorization'，实际: {module_terms}"
        )


@pytest.mark.anyio
class TestReflectionMetricsActuallyFire:
    """Task 5 review I-1 follow-up：埋点真触发测试。

    验证 _reflect_and_replan 和 _run_one_round 中的 metric 调用真的执行，
    而不是仅仅指标已注册。
    """

    async def test_reflect_and_replan_inc_triggered_metric(
        self, fake_repo_whitelist,
    ):
        """_reflect_and_replan 成功时 code_reflection_total{outcome=triggered} 应 +1。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage
        from spma.observability.code_metrics import code_reflection_total

        # 记录 inc 前的值
        before = code_reflection_total.labels(outcome="triggered")._value.get()

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

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        from spma.agents.code.explorer import ExplorerState
        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=0,
            previous_new_files=5,
        )
        await explorer._reflect_and_replan(state)

        after = code_reflection_total.labels(outcome="triggered")._value.get()
        assert after == before + 1, (
            f"code_reflection_total{{outcome=triggered}} 应 inc 1 次，"
            f"before={before}, after={after}"
        )

    async def test_reflect_and_replan_inc_failed_on_timeout(
        self, fake_repo_whitelist,
    ):
        """_reflect_and_replan 在 LLM 超时时 code_reflection_total{outcome=failed} 应 +1。"""
        from unittest.mock import AsyncMock as _AM
        from spma.observability.code_metrics import code_reflection_total

        before = code_reflection_total.labels(outcome="failed")._value.get()

        # 直接抛 TimeoutError — _reflect_and_replan 的 wait_for 会把它转成 outcome=failed
        llm = _AM()
        llm.ainvoke.side_effect = TimeoutError("simulated LLM timeout")

        ripgrep = _AM()
        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        from spma.agents.code.explorer import ExplorerState
        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=0,
            previous_new_files=5,
        )
        await explorer._reflect_and_replan(state)

        after = code_reflection_total.labels(outcome="failed")._value.get()
        assert after == before + 1, (
            f"code_reflection_total{{outcome=failed}} 应 inc 1 次，"
            f"before={before}, after={after}"
        )

    async def test_run_one_round_inc_capped_on_empty_terms(
        self, fake_repo_whitelist,
    ):
        """_run_one_round 触发 empty_terms cap 时 code_reflection_total{outcome=capped} 应 +1。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage
        from spma.agents.code.completeness import CodeCompletenessResult
        from spma.observability.code_metrics import code_reflection_total

        before = code_reflection_total.labels(outcome="capped")._value.get()

        # 反思 LLM 返回空 search_terms + drop 原 auth → 触发 empty_terms cap
        llm = _AM()
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

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        from spma.agents.code.explorer import ExplorerState
        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=2,
            previous_new_files=5,
        )
        await explorer._run_one_round(state)

        after = code_reflection_total.labels(outcome="capped")._value.get()
        assert after == before + 1, (
            f"code_reflection_total{{outcome=capped}} 应 inc 1 次，"
            f"before={before}, after={after}"
        )
        assert state.convergence.verdict == "cap"
        assert state.convergence.reason == "reflection_empty_terms"

    async def test_run_one_round_set_consecutive_no_progress_gauge(
        self, fake_repo_whitelist,
    ):
        """_run_one_round 在 new_files=0 时 consecutive_no_progress gauge 应更新。"""
        from unittest.mock import AsyncMock as _AM
        from langchain_core.messages import AIMessage
        from spma.agents.code.completeness import CodeCompletenessResult
        from spma.observability.code_metrics import code_reflection_consecutive_no_progress

        llm = _AM()
        llm.ainvoke.return_value = AIMessage(
            content=(
                '{"new_search_terms": {"module": ["authorization"]}, '
                '"drop_terms": [], "add_repos": [], "reasoning": "extend"}'
            )
        )
        ripgrep = _AM()
        ripgrep.glob_files.return_value = []
        ripgrep.search.return_value = []
        ripgrep.read_files.return_value = []  # new_files_this_round 始终为 0

        explorer = CodeExplorer(
            ripgrep_executor=ripgrep,
            ast_parser=_AM(),
            llm=llm,
            repo_whitelist=fake_repo_whitelist,
        )

        async def fake_assess(s):
            s.convergence = CodeCompletenessResult(
                verdict="progress",
                reason="diminishing_returns",
                level="L1",
                should_reflect=True,
            )

        explorer._assess = fake_assess

        from spma.agents.code.explorer import ExplorerState
        state = ExplorerState(
            round=2,
            query="q",
            entities={"module": ["auth"]},
            candidate_repos=["core"],
            search_terms={"module": ["auth"]},
            new_files_this_round=0,  # 关键：反思后无新增
            previous_new_files=0,
            consecutive_no_progress_reflections=1,  # 已累计 1 次
        )

        before_gauge = code_reflection_consecutive_no_progress._value.get()
        await explorer._run_one_round(state)
        after_gauge = code_reflection_consecutive_no_progress._value.get()

        # gauge 应被 set 为新值（state.consecutive_no_progress_reflections += 1 → 2）
        # cap 触发后 _run_one_round return，但 gauge 已被 set
        assert state.convergence.verdict == "cap"
        assert state.convergence.reason == "reflection_no_progress"
        assert after_gauge == 2, (
            f"consecutive_no_progress gauge 应被 set 为 2，"
            f"before={before_gauge}, after={after_gauge}"
        )


@pytest.mark.anyio
class TestCodeExplorerGlob:
    """spec §3.1 #7 + §6.4：_glob 多 pattern 循环、合并去重、异常隔离。"""

    async def test_glob_multi_pattern_merge(self):
        """2 个 pattern 各自返回不同 files → glob_files 被调 2 次，结果合并。"""
        executor = MockRipgrepExecutorWithData()
        executor._glob_per_pattern = {
            "**/*.java": [{"repo": "r", "file_path": "a.java"}],
            "**/*.kt": [{"repo": "r", "file_path": "b.kt"}],
        }

        # override glob_files to be pattern-aware
        async def pattern_aware_glob(pattern, repos):
            return executor._glob_per_pattern.get(pattern, [])
        executor.glob_files = pattern_aware_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1,
            query="x",
            search_terms={"glob_patterns": ["**/*.java", "**/*.kt"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        assert len(result) == 2
        paths = {r["file_path"] for r in result}
        assert paths == {"a.java", "b.kt"}

    async def test_glob_dedup_across_patterns(self):
        """同一文件被 2 个 pattern 命中 → 结果去重为 1 条。"""
        executor = MockRipgrepExecutorWithData()
        async def dup_glob(pattern, repos):
            return [{"repo": "r", "file_path": "a.java"}]  # 每个 pattern 都返回同一文件
        executor.glob_files = dup_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1, query="x",
            search_terms={"glob_patterns": ["**/*.java", "**/*Controller.java"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        assert len(result) == 1
        assert result[0]["file_path"] == "a.java"

    async def test_glob_one_pattern_fails_isolation(self):
        """第 2 个 pattern 抛 RuntimeError → 第 1 个结果保留，不抛异常。"""
        executor = MockRipgrepExecutorWithData()
        call_count = {"n": 0}
        async def flaky_glob(pattern, repos):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("ripgrep exploded")
            return [{"repo": "r", "file_path": f"a_{pattern.replace('*', 'X')}.java"}]
        executor.glob_files = flaky_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1, query="x",
            search_terms={"glob_patterns": ["**/ok.java", "**/bad.java"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        # 第 1 个成功，第 2 个失败被吞
        assert len(result) == 1
        assert "ok" in result[0]["file_path"]
        assert call_count["n"] == 2  # 两个 pattern 都尝试了
