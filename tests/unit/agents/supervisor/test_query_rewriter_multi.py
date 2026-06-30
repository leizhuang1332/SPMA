"""验证 _do_rewrite_pipeline 指代消解阶段使用编排器 + voter。"""
import pytest

from spma.agents.supervisor import query_rewriter
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.agents.supervisor.semantic_voter import SemanticVoter
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


class FakeEmbedder:
    async def embed_query(self, text): return [0.1, 0.2, 0.3]
    async def embed_documents(self, texts): return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.mark.asyncio
async def test_pipeline_uses_orchestrator_when_provided():
    """提供编排器时,指代消解走多路并行。"""
    async def _primary(q, *a, **kw):
        return q

    def _rule_only(q, *a, **kw):
        return q

    orch = StrategyOrchestrator(stage="test", names=["rule_based", "entity_based", "llm_semantic"])
    fb = FallbackManager(
        orch,
        primary_backup_fn=_primary,
        rule_only_fn=_rule_only,
    )
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)

    result = await query_rewriter._do_rewrite_pipeline(
        query="它的字段",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={"table_names": ["t_user"]},
        llm=None,
        synonym_map=None,
        conversation_history="之前聊过用户表",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
    )
    # 应至少走完整个管道,没有抛异常
    assert "resolved" in result
    # entity_based 会替换 "它" → t_user,具体结果可能为原 query 或替换后
    # 但不应崩溃


@pytest.mark.asyncio
async def test_pipeline_backward_compatible_without_orchestrator():
    """不提供编排器时,走原串行(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        # 不传 strategy_orchestrator/voter
    )
    assert "resolved" in result


# ====== P3 Task 3 code-quality review fixes: 4 new mock-based tests ======

@pytest.mark.asyncio
async def test_pipeline_orchestrator_exception_falls_back_to_resolve():
    """编排器抛错 → 降级到原 _resolve_references(返回 normalized)。"""
    from unittest.mock import AsyncMock

    async def _primary(q, *a, **kw):
        return q

    def _rule_only(q, *a, **kw):
        return q

    orch = StrategyOrchestrator(stage="test", names=["rule_based", "entity_based", "llm_semantic"])
    orch.execute_parallel = AsyncMock(side_effect=RuntimeError("orchestrator boom"))
    fb = FallbackManager(
        orch,
        primary_backup_fn=_primary,
        rule_only_fn=_rule_only,
    )
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)

    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
    )
    # 编排器抛错 → 走 _resolve_references(无 history 早退,返回 normalized)
    assert "resolved" in result
    # 验证走了 fallback(orchestrator 被调用 1 次)
    orch.execute_parallel.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_no_candidates_uses_normalized():
    """所有 strategy 返回 None → resolved = normalized。"""
    from unittest.mock import AsyncMock

    async def _primary(q, *a, **kw):
        return q

    def _rule_only(q, *a, **kw):
        return q

    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c"])
    orch.execute_parallel = AsyncMock(return_value=[("a", None), ("b", None), ("c", None)])
    fb = FallbackManager(
        orch,
        primary_backup_fn=_primary,
        rule_only_fn=_rule_only,
    )
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)

    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",  # 无代词
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
    )
    # 验证走了 fallback 路径(因为 normalize = "hello" = result["resolved"])
    assert result["resolved"] == "hello"


@pytest.mark.asyncio
async def test_pipeline_voter_exception_falls_back():
    """voter.vote_best 抛错 → 降级到 _resolve_references。"""
    from unittest.mock import AsyncMock

    async def _primary(q, *a, **kw):
        return q

    def _rule_only(q, *a, **kw):
        return q

    orch = StrategyOrchestrator(stage="test", names=["a", "b"])
    orch.execute_parallel = AsyncMock(return_value=[("a", "rewrite-A")])
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    voter.vote_best = AsyncMock(side_effect=RuntimeError("voter boom"))
    fb = FallbackManager(
        orch,
        primary_backup_fn=_primary,
        rule_only_fn=_rule_only,
    )

    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
    )
    assert "resolved" in result  # fallback 走 _resolve_references


@pytest.mark.asyncio
async def test_pipeline_voter_actually_invoked_when_candidates_exist():
    """candidates 非空 → voter.vote_best 被调用 1 次,返回 voter 的输出。"""
    from unittest.mock import AsyncMock

    async def _primary(q, *a, **kw):
        return q

    def _rule_only(q, *a, **kw):
        return q

    orch = StrategyOrchestrator(stage="test", names=["a", "b"])
    # mock 返回的 candidates 与 normalized 不同,触发 voter
    orch.execute_parallel = AsyncMock(return_value=[("a", "REWRITE-A")])
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    voter.vote_best = AsyncMock(return_value="REWRITE-A")
    fb = FallbackManager(
        orch,
        primary_backup_fn=_primary,
        rule_only_fn=_rule_only,
    )

    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
    )
    # 验证 voter 被调用
    voter.vote_best.assert_awaited_once()
    # 验证返回的是 voter 的输出
    assert result["resolved"] == "REWRITE-A"