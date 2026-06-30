"""验证 _do_rewrite_pipeline 分解阶段使用编排器 + consensus。"""
import pytest
from unittest.mock import AsyncMock

from spma.agents.supervisor import query_rewriter
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


class FakeEmbedder:
    async def embed_query(self, text): return [0.5, 0.5, 0.5]
    async def embed_documents(self, texts): return [[0.5, 0.5, 0.5] for _ in texts]


class FakeLLM:
    """最小 LLM mock —— 不期望被实际调用,但满足类型契约使 _decompose_query 走 LLM 分支。"""
    class _Resp:
        content = '[]'
    async def ainvoke(self, prompt, *a, **kw):
        return self._Resp()


# ============ Fixture:StrategyOrchestrator ============
# 为什么不是只含 3 个 P5 strategy 名称的 minimal?
# 因为 StrategyOrchestrator.__init__ 在 self._breakers 里为每个 name 预建 CB 字典,
# 而 _do_rewrite_pipeline 的 P3/P4 阶段也会对 strategy_orchestrator.execute_parallel 注入自己的
# strategies 字典。orchestrator.execute_parallel 内部以 strategies.keys() 查 _breakers,
# 不存在时会 KeyError —— 说明实现对 names 列表有"全阶段共享"假设。
# 因此 minimal orchestrator 必须包含 _do_rewrite_pipeline 三个阶段 (P3/P4/P5) 用到的所有 names。
@pytest.fixture
def minimal_orch():
    """最小 StrategyOrchestrator,覆盖 P3/P4/P5 三个阶段使用的全部策略名。

    与原 10 个 names 的硬编码 fixture 等价,但显式标注每阶段的用途,便于维护。
    """
    return StrategyOrchestrator(
        stage="test",
        names=[
            # P3 reference resolution
            "rule_based", "entity_based", "llm_semantic",
            # P4 expansion
            "intent_aware", "synonym_based", "entity_injection", "context_aware",
            # P5 decomposition
            "template_based", "entity_guided", "llm_based",
        ],
    )


def _make_fb(orch):
    """构造最小可用 FallbackManager。"""
    async def primary(q, *a, **kw): return q
    return FallbackManager(
        orch,
        primary_backup_fn=primary,
        rule_only_fn=lambda q, *a, **kw: q,
    )


@pytest.mark.asyncio
async def test_pipeline_uses_orchestrator_for_decomposition(minimal_orch):
    """提供编排器时,分解走 3 路并行 + consensus。"""
    orch = minimal_orch
    fb = _make_fb(orch)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单系统",
        classification={"query_type": "search", "sources": ["doc", "sql"], "is_cross_source": True},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    assert "sub_queries" in result
    assert len(result["sub_queries"]) >= 1


@pytest.mark.asyncio
async def test_pipeline_backward_compatible_without_orchestrator():
    """不提供编排器时,走原 _decompose_query(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc", "sql"], "is_cross_source": True},
        entities={},
        llm=FakeLLM(),
        synonym_map=None,
        conversation_history="",
    )
    assert "sub_queries" in result


# ============ 新增:4 个 mock 测试(Fix 2) ============

@pytest.mark.asyncio
async def test_pipeline_orchestrator_raises_falls_back_to_decompose(minimal_orch):
    """orchestrator.execute_parallel 抛错 → 降级到 _decompose_query。"""
    orch = minimal_orch
    # 用 side_effect 让 orchestrator 抛错
    orch.execute_parallel = AsyncMock(side_effect=RuntimeError("orchestrator boom"))
    fb = _make_fb(orch)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单系统",
        classification={"query_type": "search", "sources": ["doc", "database"], "is_cross_source": True},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    assert "sub_queries" in result
    orch.execute_parallel.assert_awaited()


@pytest.mark.asyncio
async def test_pipeline_consensus_picks_best_per_source(minimal_orch):
    """SemanticConsensusChecker.pick_best_per_source 被调用,选 per-source 共识。"""
    orch = minimal_orch

    # 选择性 mock:只在 P5(decomposition)阶段返回有效 candidates
    # P4 阶段调 execute_parallel 时给空列表,让其走 fallback
    async def fake_execute(strategies, *args, **kwargs):
        keys = set(strategies.keys())
        # P4 expansion
        if {"intent_aware", "synonym_based", "entity_injection", "context_aware"} & keys:
            return []
        # P5 decomposition
        if {"template_based", "entity_guided"} & keys or {"template_based", "entity_guided", "llm_based"} == keys:
            return [
                ("template_based", [{"query": "订单表", "target": "database"}]),
                ("entity_guided", [{"query": "订单", "target": "database"}]),
            ]
        return []

    orch.execute_parallel = fake_execute
    fb = _make_fb(orch)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["database", "doc"], "is_cross_source": True},
        entities={"table_names": ["t_order"]},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    # 验证走了 consensus 路径(返回的 sub_queries 至少包含 1 个数据库的子查询)
    db_queries = [sq for sq in result["sub_queries"] if sq.get("target") == "database"]
    assert len(db_queries) >= 1


@pytest.mark.asyncio
async def test_pipeline_no_embedder_uses_first_candidate(minimal_orch):
    """orchestrator 注入但 embedder=None → 退化到 valid[0]。"""
    orch = minimal_orch

    async def fake_execute(strategies, *args, **kwargs):
        keys = set(strategies.keys())
        if {"intent_aware", "synonym_based", "entity_injection", "context_aware"} & keys:
            return []
        if {"template_based", "entity_guided"} & keys or {"template_based", "entity_guided", "llm_based"} == keys:
            return [
                ("template_based", [{"query": "T-result", "target": "database"}]),
                ("entity_guided", [{"query": "E-result", "target": "database"}]),
            ]
        return []

    orch.execute_parallel = fake_execute
    fb = _make_fb(orch)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["database", "doc"], "is_cross_source": True},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=None,  # 无 embedder
    )
    # 无 embedder → 退化到 valid[0] = template_based 的结果
    assert result["sub_queries"][0]["query"] == "T-result"


@pytest.mark.asyncio
async def test_pipeline_no_candidates_uses_fallback(minimal_orch):
    """所有 strategy 返回空 → 走 fallback broadcast。"""
    orch = minimal_orch

    async def fake_execute(strategies, *args, **kwargs):
        keys = set(strategies.keys())
        if {"intent_aware", "synonym_based", "entity_injection", "context_aware"} & keys:
            return []
        if {"template_based", "entity_guided"} & keys or {"template_based", "entity_guided", "llm_based"} == keys:
            return [
                ("template_based", None),
                ("entity_guided", None),
                ("llm_based", None),
            ]
        return []

    orch.execute_parallel = fake_execute
    fb = _make_fb(orch)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["database", "doc"], "is_cross_source": True},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    # 所有 strategy 返回 None → 走 fallback → broadcast (每 source 一份 [{query, target}])
    assert len(result["sub_queries"]) >= 1
    targets = {sq.get("target") for sq in result["sub_queries"]}
    assert "database" in targets
