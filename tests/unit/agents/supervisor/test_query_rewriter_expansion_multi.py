"""验证 _do_rewrite_pipeline 扩展阶段使用编排器 + quality_evaluator。"""
from unittest.mock import AsyncMock

import pytest

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


@pytest.mark.asyncio
async def test_pipeline_uses_orchestrator_for_expansion():
    """提供编排器 + embedder 时,扩展走多路 + 评分。"""
    # FallbackManager 要求 primary_backup_fn 是 async(P2 Task 2 修复)
    async def primary(q, *a, **kw): return q

    orch = StrategyOrchestrator(stage="test", names=["intent_aware", "synonym_based", "entity_injection", "context_aware"])
    fb = FallbackManager(orch, primary_backup_fn=primary, rule_only_fn=lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={"table_names": ["t_order"]},
        llm=None,
        synonym_map={"订单": ["order"]},
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    # 至少有 expanded 字段
    assert "expanded" in result


@pytest.mark.asyncio
async def test_pipeline_backward_compatible_without_embedder():
    """不提供 embedder 时,走原 _expand_query 单策略(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        # 不传 embedder
    )
    assert "expanded" in result


# ====== P4 Task 3 新增:4 个 mock-based fallback 测试 ======


@pytest.mark.asyncio
async def test_pipeline_orchestrator_raises_falls_back_to_expand_query():
    """orchestrator.execute_parallel 抛错 → 降级到 _expand_query。"""
    async def primary(q, *a, **kw): return q

    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c", "d"])
    orch.execute_parallel = AsyncMock(side_effect=RuntimeError("orchestrator boom"))
    fb = FallbackManager(orch, primary_backup_fn=primary, rule_only_fn=lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    assert "expanded" in result
    orch.execute_parallel.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_embedder_raises_falls_back():
    """embedder.embed_documents 抛错 → 降级到 _expand_query。"""
    async def primary(q, *a, **kw): return q

    class BrokenEmbedder:
        async def embed_query(self, text): return [0.5, 0.5, 0.5]
        async def embed_documents(self, texts): raise RuntimeError("embedder boom")

    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c", "d"])
    orch.execute_parallel = AsyncMock(return_value=[("intent_aware", "订单 涉及")])
    fb = FallbackManager(orch, primary_backup_fn=primary, rule_only_fn=lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=BrokenEmbedder(),
    )
    assert "expanded" in result  # 走 fallback,没崩溃


@pytest.mark.asyncio
async def test_pipeline_no_candidates_uses_resolved():
    """所有 strategy 返回 None → expanded = resolved。"""
    async def primary(q, *a, **kw): return q

    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c", "d"])
    orch.execute_parallel = AsyncMock(return_value=[
        ("intent_aware", None),
        ("synonym_based", None),
        ("entity_injection", None),
        ("context_aware", None),
    ])
    fb = FallbackManager(orch, primary_backup_fn=primary, rule_only_fn=lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    # 无 candidates → expanded = resolved(走 fallback 路径)
    assert result["expanded"] == "订单"


@pytest.mark.asyncio
async def test_pipeline_picks_highest_score_candidate():
    """多候选 → 选最高分。"""
    async def primary(q, *a, **kw): return q

    class EmbedderWithVariableScores:
        """根据 text 长度返回不同 embedding,使 cosine 评分差异。"""
        async def embed_query(self, text): return [1.0, 0.0, 0.0]
        async def embed_documents(self, texts):
            # 短文本 → 高 cosine;长文本 → 低 cosine
            return [[1.0, 0.0, 0.0] if len(t) < 10 else [0.0, 1.0, 0.0] for t in texts]

    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c", "d"])
    orch.execute_parallel = AsyncMock(return_value=[
        ("intent_aware", "short"),  # 短 → 高 cosine
        ("synonym_based", "long text " * 5),  # 长 → 低 cosine
    ])
    fb = FallbackManager(orch, primary_backup_fn=primary, rule_only_fn=lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="x",  # 短 query
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=EmbedderWithVariableScores(),
    )
    # 短候选应胜出(高 cosine)
    assert result["expanded"] == "short"
